> **Read [../backup/README.md](../backup/README.md) first.** Everything about
> extracting the genuine `firmware.dat`/`firmware.tar`/`config.yaml`/
> `updater.img` from the Sony installer `.exe`, the `updater.img`/
> `checkGuard` version-bump requirement, `fwtool.py pack`'s CLI, and the
> `pmca-console.py firmware -f` flash command is documented there and is
> **not** repeated here. This doc only covers what's different when the
> firmware *content* itself is modified rather than just repacked/relabeled.

# Building a Patched Firmware Package (RX100M5, CXD90014)

## Goal
Produce a `firmware_packed.dat` that is byte-identical to stock **except**
for the specific patched bytes inside `libcamera.so` described in
[`../docs/rx100m5_hybrid_eyeaf_plan.md`](../docs/rx100m5_hybrid_eyeaf_plan.md),
flashed through the exact same validated path as
[`../backup/README.md`](../backup/README.md).

## Why this is a different (and harder) problem than the backup package
The backup package never actually decodes any partition — it repacks the
untouched `firmware.tar` straight from the Sony installer and only bumps the
version field (see backup step 3). Our case requires modifying one file
(`libcamera.so`) that lives three archive layers deep inside one partition
(`nflasha16`) inside `firmware.tar`, then reassembling every one of those
layers back into a valid, flashable form.

## The nesting problem (confirmed directly against fwtool.py, 2026-08-05)

```
firmware.tar
 └─ nflasha16          (raw partition, 82.75MB)
     confirmed via fwtool.archive.lzpt.isLzpt() → True: this is LZPT format
     └─ (LZPT-decoded) cramfs filesystem, 763 entries — includes /lib/libcamera.so
         └─ libcamera.so's cramfs entry is itself wrapped in one more archive
            layer per extract_from_partition.py's extraction log ("archive
            with 1 entries" immediately around the file) — exact sub-format
            not yet pinned down, likely cramfs's own per-file compression
            framing rather than a fourth distinct format.
```

`fwtool/archive/`'s modules are **read-only** for the formats that matter here:

| Format | Reader | Writer |
|---|---|---|
| tar (outer `firmware.tar`) | `tar.readTar` | none in fwtool — use Python's stdlib `tarfile` instead (standard format, no proprietary framing, so this is low-risk) |
| LZPT (`nflasha16`'s own wrapper) | `lzpt.readLzpt` | **none** |
| cramfs (the 763-entry filesystem inside it) | `cramfs.readCramfs` | `cramfs.writeCramfs` ✅ already exists and is reusable |

**Blocking gap (now closed for items 1-2): there was no LZPT encoder
anywhere in fwtool.py.** The only LZ77 code in the repo was
`fwtool/lz77/__init__.py`'s `inflateLz77` — a decompressor only.
`fwtool.py unpack`'s recursive `..._unpacked` folders (what
`dump-firmware/extract_from_partition.py` and manual inspection have relied
on so far) are a dead end for repacking: they exist purely for read-only
inspection, and `fwtool.py pack` only ever accepts one whole `firmware.tar`
as input — it has no mechanism to reassemble a decompressed partition tree
back into `nflasha16`'s original wrapped form.

## Required new tooling before any patch can be flashed

1. ✅ **Done.** LZ77 encoder (`fwtool/lz77/__init__.py`, `deflateLz77`).
   It does not actually compress anything — it only ever emits the format's
   literal/raw-passthrough block type (`0x0f`), splitting input into
   ≤65535-byte chunks. That's fine here; we only need byte-correctness, not
   size efficiency, since the partition has a fixed on-flash size budget
   we're already well under. Round-trip tested (`inflateLz77` decodes it
   correctly) against both synthetic data and a real 5-block slice of the
   live `nflasha16` dump.
2. ✅ **Done.** `writeLzpt(files, outFile)` (`fwtool/archive/lzpt.py`):
   mirrors `writeCramfs`'s existing style — pads the input to a multiple of
   `2**blockSize` bytes, splits it into blocks, LZ77-encodes each block,
   and writes out a fresh `LzptHeader`/`LzptTocEntry` table. Verified via a
   read → write → read round trip that reproduces the original decompressed
   bytes exactly.
3. **Still needed.** `patch_partition.py` (new, sibling to
   `dump-firmware/extract_from_partition.py`, reusing its same recursion
   logic): the mirror-image operation — walk the same nested-archive chain,
   splice in the replacement bytes for one target file, and re-serialize
   each layer outward using `writeCramfs`/`writeLzpt` above.
4. **Still needed.** Outer `firmware.tar` rebuild: use Python's stdlib
   `tarfile` module directly (fwtool has no `writeTar`) to swap in the
   modified `nflasha16` entry while copying every other partition entry
   through byte-for-byte unchanged, preserving the exact mtime/mode/uid/gid
   `tar.readTar` recorded for each.

> **Performance note:** fwtool's existing `inflateLz77`/`readLzpt` are pure
> Python and decode real (back-reference-heavy) LZ77 data slowly — a full
> `nflasha16` decode (162MB decompressed) takes long enough that
> `patch_partition.py` should decode once, cache the intermediate cramfs
> blob to disk, and only re-run the (fast, literal-only) `writeLzpt` step
> when iterating on a patch.

## Integrating the re-encoded partition into the installer firmware

Once `writeLzpt` (and eventually `patch_partition.py`) produce a new
`nflasha16` byte blob, getting that into a flashable `.dat` is **not** a new
process \u2014 it rejoins `../backup/README.md`'s already-proven pipeline at the
point right before `fwtool.py pack`:

1. **Splice the new `nflasha16` bytes into `firmware.tar`.** Open the
   original `firmware.tar` with Python's stdlib `tarfile`, iterate its
   members, and write every member through unchanged to a new tar **except**
   the `nflasha16` entry, whose contents are replaced with the
   `writeLzpt`-produced bytes (updating that member's `size`, keeping its
   `mtime`/`mode`/`uid`/`gid` as recorded). This is what `patch_partition.py`
   (tooling item 3/4 above) will automate; until then it can be done with a
   short one-off script.
2. **Hand the rebuilt `firmware.tar` to `fwtool.py pack` exactly like the
   backup package does** \u2014 same `config.yaml` (with the version field
   bumped per `../backup/README.md`'s `checkGuard` requirement), same
   `updater.img`, only the `-f` argument changes:
   ```
   fwtool.py pack -c config_patched.yaml -u unpacked/updater.img \
       -f rebuilt-firmware.tar -o patched_pack
   ```
   This produces `firmware_packed.dat`, structurally identical to a backup
   package except for the one modified partition inside it.
3. **Flash it with the same proven command** from `../backup/README.md`:
   `pmca-console.py firmware -f firmware_packed.dat`.
4. In other words: everything upstream of "hand `fwtool.py pack` a
   `firmware.tar`" is new (steps 1-2 of "Required new tooling" above);
   everything from `fwtool.py pack` onward is identical, unmodified backup
   packaging \u2014 the patch only ever changes *which bytes* go into the tar
   that gets packed, never how the tar becomes a flashable `.dat`.

## Validation plan (must pass before a single patched byte is ever flashed)

1. **No-op round trip first.** Run the full read → reserialize (no patch
   applied) → write pipeline and confirm `nflasha16`'s bytes (and ideally the
   whole rebuilt `firmware.tar`) come out identical to the original. This
   isolates bugs in the new LZ77 encoder / `writeLzpt` / cramfs rebuild /
   tar rebuild from the actual behavior patch.
2. **Then follow `../backup/README.md`'s packaging steps unchanged** —
   same `config.yaml`, same `updater.img`, same version-bump requirement,
   same `fwtool.py pack` invocation, same flash command. The only difference
   from a stock backup package is that the `firmware.tar` handed to `pack`
   now contains our modified `nflasha16` instead of the untouched original.
3. **First real patch shipped through this pipeline should be the
   debug-counter/log patch for open question #7** (see
   `../docs/rx100m5_hybrid_eyeaf_plan.md`, caveat 7's "still needed to fully
   close this out"), not the eye-AF trigger-selection change itself — this
   validates the entire tool chain on a minimal, trivially-verifiable change
   (does a counter increment or not) before risking the larger behavioral
   patch on unproven tooling.
4. **Explicit user sign-off immediately before every real flash**, same
   policy as the rest of this repo.

## Rollback
Unchanged from [`../backup/README.md`](../backup/README.md) — keep the
genuine unpacked `firmware.tar`/`config.yaml`/`updater.img` from that process
on hand, and reflash it (version-bumped above whatever the patched build
reports) if anything goes wrong.
