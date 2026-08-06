# RX100M5 Native Hybrid Eye-AF — Design & Research Plan

## Goal
Make the stock RX100M5 (RX100 V) shooting UI automatically use Eye-priority AF
(`TRIGGER_EYE_PRIORITY_AF`) instead of plain AF (`TRIGGER_AF_ON`) whenever a
face/eye is detected, as part of the *regular* AF-C loop on S1 half-press —
without requiring the user to manually switch into a separate Eye-AF button/mode,
and without a companion app. Since Eye-priority AF already tracks eyes well when
manually selected, this is a **trigger-selection change**, not a new tracking
algorithm.

## Where this lives
Entirely inside `libcamera.so`'s `CameraHardwareDiadem` class (Camera HAL for the
`cxd90014`/Diadem imaging SoC). Confirmed there is no separate installed camera
APK — the stock shooting UI runs as native code (`arbiter.so` / `libBizFw*.so` in
a separate flash partition, `nflasha15`), but it calls through this same shared
HAL library like any other client, so we don't need to reverse those stripped
binaries — patching the shared library changes behavior for whichever process
calls it.

## Confirmed facts (via Ghidra static analysis + on-device testing)

- `sendCommand(0x10000037, start, triggerType)` → `executeAutoFocusStartTrigger`
  — vendor entry point that explicitly starts/stops AF with a specific trigger
  type. Validates `triggerType` in `{3,4,5,6,7,8}`.
- Exact `TRIGGER_*` int mapping (found via raw binary scan of the string table
  at file offset `0x34394`):
  - `3` = `TRIGGER_AFL`
  - `4` = `TRIGGER_EYE_START`
  - `5` = `TRIGGER_AF_MF_CHANGE`
  - `6` = `TRIGGER_ENTER`
  - `7` = `TRIGGER_AF_ON`
  - `8` = `TRIGGER_EYE_PRIORITY_AF`
- Bare `autoFocus()` (the standard Android HAL hook) sends `SDComMsg` cmd
  `0x1006` with **no explicit trigger param** — it relies on an internal
  stored/default trigger state rather than always meaning `TRIGGER_AF_ON`.
- `handleFaceDetectedEvent` parses DSP face-detection results (position, size,
  angle, blink/smile) into a face array and forwards them via the Camera data
  callback (msg type `0xe`) when enabled — this is the source we'd read from to
  decide "is a face present right now."
- `handleSTDFocusEvent` is the actual source of the AF-lock cursor confirmation
  (`notify(CAMERA_MSG_FOCUS, success, 0, cookie)`), independent of which
  trigger type was used — confirms switching trigger type won't break the
  existing lock-confirmation UI behavior.
- The HAL exposes distinct `"af-c"` / `"af-s"` / `"single"` parameter-mode
  strings, confirming AF-S vs AF-C is tracked as a separate concept from
  trigger type. The patch must only apply continuous-style re-selection in
  AF-C; AF-S should behave as a normal single lock-and-hold (optionally biased
  toward eye once, at acquisition time only).
- Candidate static state fields (address offsets relative to this build):
  `s_autoFocusMode` (AF-S/AF-C/etc.), `s_autoFocusStartTrigger` (the stored
  default trigger `autoFocus()` uses), `s_quickAutoFocus`. Each has exactly one
  reference in the binary, but that reference is **not inside a decompiled
  function** (likely a data table entry, e.g. a parameter-name → field-offset
  mapping table, same pattern as the focus-area-type table found earlier) —
  the actual code that reads/writes them via that table was not pinned down in
  this pass.
- **MAJOR FINDING (static analysis, no hardware needed): `executeAutoFocusStartTrigger`
  (@ `0x2f03c`) is the *only* function in the entire binary that can trigger
  case `0x10000037`, and case `0x10000037` is the *only* caller of
  `executeAutoFocusStartTrigger`.** Confirmed via exhaustive Ghidra
  cross-reference search (every reference to the function's entry point) AND
  an exhaustive scan of every instruction operand in the binary for the
  literal constant `0x10000037` — zero other hits. This means the choice of
  trigger value (7 vs 8) is **not decided anywhere inside `libcamera.so`** —
  it is simply whatever int the external caller (stripped BizFw/arbiter code)
  passes in. The function itself is a thin ~40-instruction pass-through:
  validate `param_2` (trigger type) is in `{3,4,5,6,7,8}`, package it as
  `SDComMsgIntParam(0x42, param_2)`, and forward via `SDComMsg_Basic(this,
  0x1006 /*start*/ or 0x1007 /*stop*/, ...)` down to the DSP/lower IPC layer.
  **This changes the whole patch strategy for the better**: we don't need to
  find or understand any AF-C decision logic in the (stripped, unreachable)
  caller — we can intercept and override the trigger value right here, at
  this single always-executed choke point, regardless of what the caller
  intended.
- **`handleFaceDetectedEvent`'s exact confidence-gating condition located** (@
  `0x36040`): a per-detection confidence/score value (`local_88`, sourced from
  an `SDComMsg` param of type `0x13`) is compared against a fixed threshold of
  `0x30` (48). If `local_88 < 0x30` **or** the face count (`local_8c`) is
  `<= 0`, the function takes its "no faces" bail-out path
  (`LAB_000362c0`/`LAB_000362c8`) without building any face array or emitting
  the `0xe` data callback. This is the precise, single condition to hang a new
  cached "face currently present" flag off of: set the flag in the branch
  where `local_88 >= 0x30 && local_8c > 0` (right before the face array is
  built/emitted), and clear it in the bail-out branch.

## Real-time Eye-AF vs. this design (context from prior analysis)
Modern Sony real-time Eye AF = (a) a continuous, every-frame subject-tracking
loop (this architecture is *not* new/AI-specific — it's the same Lock-on AF
loop going back to the a6000 era) + (b) an AI-trained recognizer feeding that
loop the eye position each frame + (c) dedicated front-end-LSI hardware to run
both at up to ~60/sec. We are **not** trying to replicate (b) or (c). Because
`TRIGGER_EYE_PRIORITY_AF` already exists as a first-class, well-tracking DSP
trigger on this camera, we only need to auto-*select* it — the DSP's own
internal continuous behavior for that trigger (whatever it already is) carries
over unmodified. This meaningfully de-risks the project vs. earlier drafts of
this plan that considered hijacking the generic `startTrackingFocus` /
Lock-on-AF primitive (unverified whether that generic tracker stays precisely
pinned to an eye — no longer needed).

## Open questions / caveats still to resolve before writing a byte-level patch

1. **~~Exact hook point for trigger selection is not yet located.~~ RESOLVED
   (reframed).** We now know the patch does not need to find or emulate any
   AF-C "decision" logic at all, because that logic doesn't live in
   `libcamera.so` — it lives in the stripped BizFw/arbiter caller. Instead,
   the patch intercepts at the single, always-executed choke point,
   `executeAutoFocusStartTrigger` (@ `0x2f03c`): at entry, if `param_2 == 7`
   (`TRIGGER_AF_ON`) and our cached face-present flag is set, rewrite
   `param_2` to `8` (`TRIGGER_EYE_PRIORITY_AF`) before the existing
   validate/forward logic runs. No other function needs to be touched for
   trigger selection. Remaining sub-question: confirm (ideally via Tier 1/2
   on-device logging when available) that BizFw does in fact pass `7` for a
   normal S1 half-press in AF-C mode (assumed, not yet directly observed).
2. **AF-C re-fire cadence — now lower-risk than previously assessed.**
   Because the override happens at the single always-executed choke point
   rather than requiring us to hook a loop, the cadence question no longer
   affects *correctness*, only *responsiveness*: if BizFw only calls this
   once per half-press, the override simply makes that one call eye-priority
   whenever a face happened to be present at that instant — still a strict
   improvement over today, never worse than today. If BizFw re-fires
   periodically while S1 is held, responsiveness to a face appearing mid-hold
   improves further for free, with no patch changes needed either way.
3. **Face-detection cadence is unmeasured.** If `handleFaceDetectedEvent`
   only fires a few times/sec rather than every frame, the cached face-flag
   used by the trigger override will lag behind real subject motion — needs
   on-device/log-based measurement, not just static analysis.
4. **AF-S vs AF-C gating not yet wired to a confirmed enum value — now
   optional for v1.** We still have not confirmed the exact integer value(s)
   `s_autoFocusMode` takes for AF-C vs AF-S, and the only references to that
   field resolve to an unresolved data-table entry rather than a decompiled
   call site (same table-driven-parameter-parser pattern as the `af-c`/`af-s`
   strings, which also have zero direct code cross-references). Given
   finding #1 above, mode-gating is no longer required for a correct/safe v1:
   the override can simply upgrade any `param_2 == 7` request to `8` whenever
   a face is present, regardless of AF-S/AF-C — worst case in AF-S this makes
   a single-shot lock eye-biased once, which is arguably desirable anyway.
   Recommend shipping v1 without mode-gating and only add it later if
   real-world testing shows an unwanted AF-S side effect.
5. **Multi-face / face-selection behavior unspecified.** If multiple faces
   are detected, need to decide (or discover existing DSP behavior for) which
   one biases trigger selection — likely reuse whatever the existing manual
   eye-priority AF trigger already does internally (untouched), so this may be
   a non-issue, but should be confirmed. Note `handleFaceDetectedEvent` does
   already loop over all detected faces (`local_8c` count) when building the
   face-info array, so a future refinement could plumb through "largest
   face"/"most-centered face" if the DSP's own eye-priority AF doesn't already
   pick sensibly among multiple faces on its own.
6. **Firmware modification risk.** This still requires a real binary patch to
   a shared library loaded early by the imaging pipeline. A bad patch (wrong
   branch offset, corrupted stack frame, misaligned instruction) risks
   crashing/hanging the camera's imaging subsystem on every boot. No patch
   should be written or flashed without:
   - A confirmed rollback plan (verified stock-firmware reflash path via
     `fwtool.py`/Sony-PMCA-RE).
   - Understanding whether Sony's updater validates a signature/checksum over
     individual libraries or the whole firmware image, and whether that would
     reject or (worse) silently mis-flash a modified image.

   **RESOLVED (2026-08-05, via init.rc analysis): a crashing `libcamera.so`
   is very unlikely to block entry into updater mode.** Entering updater mode
   (`pmca-console`'s `switchMode()`) requires the device to boot far enough
   to expose USB/MTP and respond to a vendor command over that session — the
   original open question was whether a bad patch to `libcamera.so` could
   prevent that. Checked
   `nflasha15_unpacked_unpacked/bin/ramdisk.img_unpacked_unpacked/init.rc`
   (+ `init.usb.rc`) for the actual process/service topology:
   - `libcamera.so` is loaded inside the `media` service
     (`/system/bin/mediaserver`, `class main`) — **not** marked `critical`,
     and no other service's `onrestart` depends on `media` surviving (only
     the reverse: `servicemanager`/`zygote` restarting also restarts `media`
     as a side effect, one-directional). A crash loop in `mediaserver` just
     keeps getting restarted by `init` — it does **not** cascade to `zygote`,
     `surfaceflinger`, `servicemanager`, or `init` itself, and does **not**
     trigger the "4 crashes in 4 minutes -> full device reboot" watchdog
     (that only applies to services flagged `critical`, e.g. `ueventd`/
     `servicemanager`).
   - USB gadget mode switching (`sys.usb.config` property -> writes to
     `/sys/class/android_usb/android0/*`: `enable`, `idVendor`, `idProduct`,
     `functions`) is handled directly by `init` (PID 1) via built-in
     property-trigger rules in `init.usb.rc` — entirely independent of
     `mediaserver`/camera HAL. The actual MTP protocol responder almost
     certainly lives in `system_server` (spawned by `zygote`), also a
     separate process tree from `mediaserver`.
   - Conclusion: a segfault/hang inside `libcamera.so` (a userspace .so
     loaded only by `mediaserver`) should only kill and restart that one
     process — it has no plausible path to prevent USB enumeration, MTP
     responses, or `switchMode()` from working. Residual risk: this analysis
     covers *userspace* crashes; a patch bad enough to corrupt kernel memory
     via a driver `ioctl` (not expected for this kind of trigger-value
     patch) is a different, much less likely failure mode not fully ruled
     out. Still validate the patch is byte-perfect and keep
     `rollback_to_v200`/equivalent ready before any real flash.
   - Explicit user sign-off immediately before any actual flashing step.
8. **CONFIRMED (on-device, Tier 2): raw MSC `push` to a live system partition
   does NOT work.** Attempted `push <local> /dev/nflasha16` (with an
   unmodified dump, to test the round-trip) failed partway through
   (`Mass storage error: Sense 0x2 0xff 0xff`, SCSI NOT READY). Re-reading the
   partition afterward (see flash-dump notes) is required to confirm no
   corruption occurred, but functionally: **raw block-level writes to system
   partitions are not a valid flashing path** through this tool/protocol.
   The correct mechanism (confirmed via source inspection, not yet
   end-to-end tested) is `fwtool.py pack` (builds a `.fdat`/`.dat` firmware
   update package from a `firmware.tar`), flashed via pmca-console's already-
   proven `writeFirmware()`/`CMD_WRITE_FIRM` vendor protocol — the same
   mechanism that already successfully flashed the small `updater.img` payload
   that got us the live shell in the first place. Any future patch must go
   through repack-and-flash (`fwtool.py pack` → `pmca-console updatershell -f
   <custom.dat>`), not a raw partition overwrite. This also means the
   rollback plan should be: pack the **unmodified** original `firmware.tar`
   the same way and flash it back through this same validated path, rather
   than relying on restoring a raw `dd`-style partition dump.
7. **BizFw/arbiter calling assumptions — PARTIALLY RESOLVED (2026-08-05, via
   binary dependency analysis), and the answer changes the risk picture.**
   Originally assumed the stock shooting UI calls into `CameraHardwareDiadem`
   the same way a generic Android Camera HAL client would (i.e., through
   `autoFocus()`/`sendCommand()` over the standard Binder `ICamera`/
   `ICameraService` path). Checked this directly by extracting and inspecting
   the dynamic dependencies (`DT_NEEDED`, via `objdump -p`) and dlopen/string
   references of every binary in the native shooting-UI stack
   (`arbiter.so`, `libBizFw.so`, `libBizFw2.so`, `libMWF.so`,
   `dataflowInfraFramework.so` — all in `nflasha15`):
   - **None of them link or dlopen `libbinder.so`, `libcamera_client.so`, or
     `libcameraservice.so`** (all three of which exist, but only in
     `nflasha16` alongside `libcamera.so` itself). They only depend on a
     custom message-queue IPC layer (`libosal_uipc.so` —
     `osal_snd_msg`/`osal_reg_msg_queue_cb`/etc.) and a proprietary hardware
     object framework (`libMWF.so`/`dataflowInfraFramework.so`). `libBizFw.so`
     exposes demangled C++ symbols for hardware "channel" objects
     (`OBJAVBBCBASECLASS::VdfCh`, `AdfCh`, `SdiCh`, `SdfCh`, `StmCh`, `TcubCh`,
     etc.) and a literal `ConvertResMidFromCameraLiro` symbol, confirming
     BizFw talks to the imaging pipeline through this same proprietary
     "AVBBC" object/message framework, not through Android's Camera API.
   - `nflasha16` separately hosts a small `libarbiter_proxy.so` (exported
     symbols `ABT_reg_resource`/`ABT_set_state`/`ABT_reg_observer`/
     `ABT_PRXY_init`) that **also** links `libosal_uipc.so` — almost
     certainly the bridge that lets whatever process hosts `libcamera.so`
     (i.e. `mediaserver`) participate in the same OSAL message bus BizFw
     uses, independent of Binder.
   - Confirmed via `init.rc`/`init.usb.rc` location that `nflasha15` and
     `nflasha16` share one single kernel/init instance (only one `init.rc`
     exists, in `nflasha15`'s ramdisk; `nflasha16` is just its `/system`
     mount) — so this isn't two separate machines, just two different IPC
     mechanisms available within the same OS.
   - **Practical implication (not yet empirically confirmed): the physical
     shutter-button/native-UI AF-C loop most likely does *not* go through
     Android's standard `autoFocus()`/`sendCommand()` Binder path at all** —
     it likely talks to the DSP directly over this OSAL/arbiter bus. The
     `executeAutoFocusStartTrigger`/`sendCommand(0x10000037)` path may only
     be reachable from genuine Android Camera-Binder clients (USB tethered
     shooting, Wi-Fi remote apps, MTP control), not from the physical
     shutter button. This is a materially different conclusion than this
     doc's original "Where this lives" framing ("it calls through this same
     shared HAL library like any other client").
   - **Further static evidence (2026-08-05, round 2) — now strongly
     (not just circumstantially) pointing the same direction.** Checked the
     two remaining plausible bridge points directly:
     - `mediaserver`'s own `DT_NEEDED` list (`objdump -p`) is a stock Android
       set (`libaudioflinger.so`, `libcameraservice.so`,
       `libcameraexservice.so`, `libmediaplayerservice.so`, ...) — it does
       **not** link `libosal_uipc.so` or `libarbiter_proxy.so` itself.
       `libcameraexservice.so` and `libcameraservice.so` likewise only pull
       in standard Binder/media libs (`libbinder`, `libcamera_client`,
       `libmedia`, `libgui`, `libhardware`, ...) plus `libcamera.so` —
       nothing OSAL/arbiter-related anywhere in that chain.
     - `libcamera.so` itself has **zero** string references to
       `osal`/`arbiter`/`ABT_`/`bizfw`/`avbbc` (checked via `strings`), and
       the only device node it opens is `/dev/stream2` (a V4L2-style
       streaming character device).
     - `libBizFw.so`, by contrast, opens **`/dev/mem`** directly and exports
       an `AvbbcPhycMem` C++ class (`GetLogAddr`/`MemSet`/cache-mode
       constructor) — i.e. BizFw pokes hardware registers via raw
       memory-mapped I/O, completely bypassing the kernel driver model that
       `/dev/stream2` (and therefore `libcamera.so`) relies on. `arbiter.so`
       and `libBizFw2.so` reference no device nodes at all (pure IPC/logic
       glue).
     - Conclusion: two entirely disjoint hardware-access mechanisms, with no
       shared library link, no shared device node, and no string
       cross-references in either direction. Static analysis cannot
       *prove* a negative (BizFw could still reach `libcamera.so`'s process
       via a Binder client registered elsewhere, or the two mechanisms could
       converge on the same physical AF-motor register from opposite ends),
       but every static signal now points the same way: the physical
       shutter/AF-C loop almost certainly drives the lens/AF hardware
       directly through BizFw's `/dev/mem` MMIO path, not through
       `libcamera.so`'s `sendCommand()`/`executeAutoFocusStartTrigger`.
   - **Still needed to fully close this out**: static analysis has reached
     its practical limit here — the remaining uncertainty (does anything,
     anywhere, call from the BizFw/AVBBC side into `libcamera.so`'s process
     at runtime via a mechanism invisible to `objdump`/`strings`, e.g. a
     runtime-registered Binder callback) can only be settled by an
     empirical, on-device test. Cheapest test: patch a debug counter/log
     line at the entry of `executeAutoFocusStartTrigger` (no behavior
     change), reflash, and observe (via a log partition or any available
     on-device logging) whether it increments on a normal S1 half-press in
     AF-C mode, versus only when triggered from a tethered/remote
     Android-side client. If it does *not* increment on a physical
     half-press, the whole patch needs to be retargeted to wherever BizFw's
     own AVBBC/OSAL AF-trigger dispatch happens instead (which would require
     reverse engineering the stripped `libBizFw.so`, a substantially larger
     undertaking than originally scoped).

## Proposed patch shape (single choke-point design)


1. Add a new global/static byte, `g_facePresent`, in a code cave or unused
   `.bss` padding.
2. In `handleFaceDetectedEvent` (@ `0x36040`), at the branch already computed
   from `local_88`/`local_8c` (confidence `>= 0x30` and face count `> 0`),
   add one store: `g_facePresent = 1`. In the existing "no faces" bail-out
   path (`LAB_000362c0`), add: `g_facePresent = 0`. No other logic in this
   (large, delicate) function needs to change.
3. In `executeAutoFocusStartTrigger` (@ `0x2f03c`), immediately after entry,
   insert: `if (param_2 == 7 && g_facePresent) param_2 = 8;` — before the
   existing `{3,4,5,6,7,8}` validation/dispatch logic, which is otherwise
   untouched. This is the entire trigger-selection change; no AF-C/AF-S mode
   gating needed for v1 (see open question #4).
4. Leave `handleSTDFocusEvent`, `handleFocusDoneEvent`, and all other event
   handlers untouched — confirmed independent of trigger type.
5. No changes to `startTrackingFocus`/`handleTrackingFocusInfoEvent` are
   needed under this simplified design.

This is a much smaller patch surface than originally scoped: two small edits
(a couple of instructions each) in two already-fully-understood functions,
with no dependency on reverse-engineering the stripped BizFw/arbiter binaries
at all.

## Open question #7 test plan: debug-counter patch

See [`debug_counter_patch_plan.md`](debug_counter_patch_plan.md) for the full
design (target/mechanism, log destination, raw-syscall implementation, test
protocol, interpretation criteria, and cleanup) — this settles, empirically,
whether `executeAutoFocusStartTrigger` is actually reached on a physical S1
half-press in AF-C mode, or only from a tethered/remote client (see the
"still needed to fully close this out" note above).

## Next steps
1. Find a safe code-cave (unused padding/alignment bytes) near each patch
   site, or confirm free space at the end of `.bss`/`.data` for
   `g_facePresent`, via raw file/section-header inspection.
2. Draft the exact ARM (Thumb vs ARM mode — confirm which this function is
   compiled as) instruction sequence for both edits, including the branch
   needed to reach the code cave and back if the insertion doesn't fit inline.
3. Confirm face-detection cadence via on-device logging (Tier 2 live shell
   access is now working on this Mac via a community Apple-Silicon `OS-X-MSC`
   driver — see `/memories/macos_usb_tools.md`); not required to draft the
   patch itself, only to validate real-world responsiveness afterward.
4. Before any flashing: prove the `fwtool.py pack` → `updatershell -f
   <custom.dat>` round trip works end-to-end with an **unmodified** repacked
   firmware.tar first (byte-identical boot/behavior), establishing a real,
   validated rollback path — do this before ever flashing a patched
   `libcamera.so`. Raw partition `push` is confirmed NOT viable (see caveat
   #8), so this repack-based flash is the only path forward.
5. Only once the repack/flash round trip is proven: draft and flash the
   actual patched `libcamera.so`, with explicit user go-ahead immediately
   before the real flashing step.
