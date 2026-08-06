# Debug-Counter Patch Plan (Open Question #7)

See [`rx100m5_hybrid_eyeaf_plan.md`](rx100m5_hybrid_eyeaf_plan.md) for the
full trigger-override patch design this test validates. This doc covers only
open question #7: settling, empirically, whether `executeAutoFocusStartTrigger`
(the function the trigger-override patch hooks) is actually reached on a
**physical** S1 half-press in AF-C mode, or only from a tethered/remote
Android-side client — see that doc's "still needed to fully close this out"
note. This is a pure observation patch: it changes **no behavior**, only
appends a small record to a persistent log file so the result can be read
back from a partition dump with no adb/companion app involved (adb on this
camera only works from inside the OpenMemories tweak app and doesn't survive
app exit, so an on-device log file — not `logcat` — is the only viable
capture mechanism here).

## Target and mechanism
- Patch site: entry of `executeAutoFocusStartTrigger` @ `0x2f03c` in
  `libcamera.so` (ARM32, EABI5, stripped, confirmed via `file`/`objdump -h`;
  Thumb-vs-ARM mode for this function still needs confirming via
  disassembly before the exact byte patch is written).
- The insert does one thing: append 2 bytes to a log file, then fall through
  to the function's existing, untouched logic. No parameters, control flow,
  or trigger values are altered — this must have zero effect on camera
  behavior, so any observed difference in later testing is attributable only
  to the *next*, real trigger-override patch, not to this one.

## Log destination: `/log/af_trigger_dbg.bin`
- `/log` is `/dev/nflasha11`, a dedicated 6MB vfat partition (confirmed via
  `0100_config/mount.conf`), already used by stock firmware at runtime for
  `basic.prof_path=/log/ssbi_prof.bin` (`0800_appli/setting/kemco.txt`) — so
  it's a real, actively-mounted-rw, persistent destination, not a
  provisioning-only artifact.
- **Confirmed not wiped by flashing**: `firmware.tar_unpacked/0700_part_image/dev/`
  only contains partition images for `nflasha3`, `nflasha5`, `nflasha7`,
  `nflasha15`, `nflasha16` — `nflasha11` is absent from the flashed set
  entirely, so installing the patched `firmware_packed.dat` never touches
  `/log`. The counter file survives across the very flash that installs the
  patch that's being tested.
- vfat has no real Unix permission model and `/log` sits outside `/android`,
  so it should be reachable from `libcamera.so`'s native process without
  Android-level sandboxing concerns (unconfirmed until tested).
- Capacity is a non-issue: each call appends 2 bytes; even hundreds of test
  presses is a few hundred bytes against 6MB.

## Implementation: raw syscalls, no new ELF imports
`libcamera.so` already imports `open`/`close`/`snprintf`/`memcpy`/`strlen`
(confirmed via `objdump -p`'s dynamic symbol table) but **not** `write`.
Adding `write` as a new dynamic import would grow `.dynsym`/`.dynstr`/`.plt`
and break the same-size, minimal-diff patch property that
`patch/patch_partition.py` relies on (it can only overwrite existing file
bytes in place, not resize/reallocate ext2 blocks). Instead, the log-write
code cave uses raw ARM syscalls directly (`svc #0`), bypassing libc/PLT
entirely for this one operation:
- `open` = syscall 5, `write` = syscall 4, `close` = syscall 6 (standard ARM
  EABI numbers, `r7` = syscall number, `r0`-`r2` = args, `svc #0` to invoke).
- Sequence: `open("/log/af_trigger_dbg.bin", O_WRONLY|O_CREAT|O_APPEND, 0666)`
  → `write(fd, &record, 2)` → `close(fd)`, then branch back to the original
  function entry to continue normal execution unmodified.
- Record format: 1 incrementing counter byte (wraps at 256) + 1 tag byte
  (reserved for trigger-source context if a cheap way to distinguish it is
  found later; currently just a constant marker byte).

## Test protocol
1. Build the patched `libcamera.so` via the raw-syscall insert above (no
   other bytes changed), same length as the original (215548 bytes).
2. Round-trip it through `patch/patch_partition.py` (in-place ext2 byte
   patch) → `patch/splice_into_tar.py` (splice into `firmware.tar`) →
   `fwtool.py pack` → flash via `pmca-console.py firmware -f`, per
   [`../patch/README.md`](../patch/README.md). Explicit user go-ahead
   required immediately before the real flash, per repo policy.
3. On the physical camera, with **no** tethered/remote client connected: do
   5 brief AF-C half-shutter (S1) holds.
4. Power off the camera. Pull `/dev/nflasha11` (**not** `nflasha16` — the
   log lives on its own partition) via
   `pmca-console.py serviceshell` → `pull /log/af_trigger_dbg.bin`, or the
   whole raw partition if easier to inspect with `fwtool.py`/a hex viewer.
5. Read the counter directly — no adb, no logcat, no companion app needed.

## Interpretation
- Counter advanced by ~5: confirms `executeAutoFocusStartTrigger` **is** on
  the physical S1/AF-C path — proceed with the full trigger-override patch
  exactly as designed in `rx100m5_hybrid_eyeaf_plan.md`.
- Counter stayed at 0 (or didn't advance during the physical-only test):
  confirms the physical half-press bypasses this function entirely — the
  trigger-override patch must be retargeted to wherever BizFw's own
  AVBBC/OSAL AF dispatch happens instead, which requires reverse engineering
  the stripped `libBizFw.so` (a substantially larger undertaking than
  originally scoped, per that doc's "still needed to fully close this out"
  note).

## Cleanup between test iterations
Not required for capacity reasons (see above), but if a clean slate is
wanted: `pmca-console.py serviceshell` → `shell` (a real interactive shell —
`UsbPlatformBackend` implements `ShellPlatformBackend`) → `rm
/log/af_trigger_dbg.bin`. Pull-and-archive the file before deleting if the
raw byte history is worth keeping across test rounds.

## Status
Design finalized; disassembly of the exact patch site (Thumb vs ARM mode,
code-cave location) not yet done — this is the next concrete step before any
bytes are written. Fresh firmware unpacked for this work at
`patch/PATCH-DEBUG-COUNTER/` (sourced from the genuine installer `.dat`, not
the live device dump, per `../patch/README.md`'s source-of-truth guidance).
