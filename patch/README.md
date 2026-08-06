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
(`libcamera.so`) that lives inside one partition (`nflasha16`) inside
`firmware.tar`, then reassembling that partition back into a valid,
flashable form.

## The nesting problem (confirmed directly against fwtool.py and a real
device dump, 2026-08-05 - corrected from an earlier, wrong guess)

```
firmware.tar
 └─ nflasha16          (raw partition, 82.75MB)
     confirmed via fwtool.archive.lzpt.isLzpt() → True: this is LZPT format
     └─ (LZPT-decoded) ext2 filesystem, 763 entries, 162MB
         └─ /lib/libcamera.so, stored raw (215548 bytes, no further wrapping)
```

An earlier pass at this doc guessed the middle layer was cramfs with a
fourth, unidentified wrapper around `libcamera.so` specifically. Both were
wrong: traced directly against `nflasha16_live.img` with instrumented
`fwtool.archive` format detection, the middle layer is **ext2**, and
`/lib/libcamera.so` sits directly in it with no further wrapping (some
*other*, unrelated files in that same ext2 image are individually
gzip-compressed, which is what caused the earlier "one more archive layer"
misreading — that gzip layer belongs to a different file, not this one).

`fwtool/archive/`'s modules are **read-only** for the formats that matter here:

| Format | Reader | Writer |
|---|---|---|
| tar (outer `firmware.tar`) | `tar.readTar` | none in fwtool — use Python's stdlib `tarfile` instead (standard format, no proprietary framing, so this is low-risk; see `splice_into_tar.py`) |
| LZPT (`nflasha16`'s own wrapper) | `lzpt.readLzpt` | **none in fwtool** — added in `fwtool/archive/lzpt.py`'s `writeLzpt` |
| ext2 (the 763-entry filesystem inside it) | `ext2.readExt2` | **none in fwtool, and not worth writing** — a real ext2 writer needs full block/group-descriptor/bitmap allocation logic. Instead `patch_partition.py` does an **in-place same-size byte patch**: it resolves the target file's existing data blocks via `ext2.py`'s own inode/block-pointer structs and overwrites them directly, requiring the new content be exactly the same length as the original (true for instruction/constant-level patches, which don't add code). |

## Source of truth: the installer's `firmware.tar`, not the live device dump

[`../dump-firmware/`](../dump-firmware) pulls a *live* `nflasha16` straight off
a camera (`nflasha16_live.img`) and can extract `libcamera.so` from it. That
live dump is **only** used for two things: (1) validating that our archive
tooling reads real device data correctly, and (2) providing real
(non-synthetic) compressed bytes to stress-test `deflateLz77`/`writeLzpt`
against. [`../dump-firmware/README.md`](../dump-firmware/README.md#verifying-a-dump-is-genuine)'s
SHA-256 comparison against the installer-unpacked copy is exactly this check
— confirming the live dump matches the official package, nothing more.

**The patch itself must be built against `nflasha16` from the *same*
`firmware.tar` that `config.yaml`/`updater.img` come from** (i.e. unpacked
from the Sony installer `.exe` per `../backup/README.md`), never from a
re-derived live dump. Splicing a live-pulled partition into an otherwise
installer-sourced package risks a version/build mismatch between partitions
that the SHA-256 spot-check on one file wouldn't catch. `patch_partition.py`
(below) always takes the unpacked `firmware.tar` as its input, matching
`backup/README.md`'s process exactly — the live dump never enters the build
pipeline, only its role as a cross-check for extraction correctness.

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
3. ✅ **Done.** `patch/patch_partition.py` (sibling to
   `dump-firmware/extract_from_partition.py`, importing `fwtool.archive.ext2`'s
   own structs so block-pointer resolution stays in sync with fwtool.py):
   decodes `nflasha16` via `lzpt.readLzpt`, resolves the target file's inode
   and direct/indirect data blocks by walking the ext2 directory tree from
   the root inode, overwrites those blocks in place with the new
   same-length content, and re-encodes via `lzpt.writeLzpt`. Refuses with a
   clear error if the new content isn't exactly the same size, or if the
   file has a sparse hole (neither case is supported without a real ext2
   block allocator).
4. ✅ **Done.** `patch/splice_into_tar.py`: uses Python's stdlib `tarfile`
   to replace one named member's bytes in `firmware.tar` while copying every
   other member through unchanged (verified members' size/mtime/mode/uid/gid
   are preserved exactly for untouched entries).

**Tested against the real `nflasha16_live.img` (2026-08-05):**
- No-op round trip (patch `/lib/libcamera.so` with its own unmodified
  bytes): decoded ext2 image byte-identical to the original, `libcamera.so`
  and all 763 ext2 entries unchanged, LZPT re-decode matches.
- Real single-byte modification: correctly appears at the exact patched
  offset in the final decoded output, every byte outside the patch
  (including the rest of `libcamera.so` and all 762 other ext2 entries)
  verified byte-for-byte unchanged.
- `splice_into_tar.py` tested against a synthetic multi-member tar: target
  member replaced with new (differently-sized) content, other members'
  bytes/mtime/mode preserved exactly.

> **Performance note:** fwtool's `inflateLz77`/`ChunkedFile` had two O(n²)
> bugs (repeated `bytes +=` instead of `bytearray`) that made a full
> `nflasha16` decode take 379s; both are now fixed in the `JEF1056/fwtool.py`
> fork, bringing a full real decode down to ~9.5s. `patch_partition.py`'s
> whole pipeline (decode → patch → re-encode → re-decode to verify) runs in
> well under a minute end-to-end on the real partition.

## Usage

```
# 1. Extract the target file, apply your actual patch to it however you like
#    (e.g. a hex editor, a small Python script, Ghidra's patch/export),
#    producing a same-length modified copy.
python3 ../dump-firmware/extract_from_partition.py nflasha16.img libcamera.so libcamera.so.orig
# ... produce libcamera.so.patched, same byte length as libcamera.so.orig ...

# 2. Patch it into the partition image
python3 patch_partition.py nflasha16.img /lib/libcamera.so libcamera.so.patched nflasha16_patched.img

# 3. Splice the patched partition into firmware.tar
python3 splice_into_tar.py firmware.tar nflasha16 nflasha16_patched.img firmware_patched.tar

# 4. From here on, follow ../backup/README.md exactly, substituting
#    firmware_patched.tar for the untouched firmware.tar.
```

## Integrating the re-encoded partition into the installer firmware

`patch_partition.py` and `splice_into_tar.py` (above) produce a new
`firmware.tar` with the patched partition spliced in. Getting that into a
flashable `.dat` is **not** a new process — it rejoins `../backup/README.md`'s
already-proven pipeline at the point right before `fwtool.py pack`:

1. **Splice the new `nflasha16` bytes into `firmware.tar`** — done by
   `splice_into_tar.py` (step 3 in Usage above): every member is copied
   through unchanged except `nflasha16`, whose contents are replaced with
   `patch_partition.py`'s output (updating that member's `size`, keeping its
   `mtime`/`mode`/`uid`/`gid` as recorded).
2. **Hand the rebuilt `firmware.tar` to `fwtool.py pack` exactly like the
   backup package does** — same `config.yaml` (with the version field
   bumped per `../backup/README.md`'s `checkGuard` requirement), same
   `updater.img`, only the `-f` argument changes:
   ```
   fwtool.py pack -c config_patched.yaml -u unpacked/updater.img \
       -f firmware_patched.tar -o patched_pack
   ```
   This produces `firmware_packed.dat`, structurally identical to a backup
   package except for the one modified partition inside it.
3. **Flash it with the same proven command** from `../backup/README.md`:
   `pmca-console.py firmware -f firmware_packed.dat`.
4. In other words: everything upstream of "hand `fwtool.py pack` a
   `firmware.tar`" is new (`patch_partition.py` + `splice_into_tar.py`);
   everything from `fwtool.py pack` onward is identical, unmodified backup
   packaging — the patch only ever changes *which bytes* go into the tar
   that gets packed, never how the tar becomes a flashable `.dat`.

## Validation plan (must pass before a single patched byte is ever flashed)

1. ✅ **No-op round trip — done.** Read → reserialize (no patch applied) →
   write pipeline confirmed identical to the original: `nflasha16`'s decoded
   ext2 bytes, `libcamera.so`, and all 763 ext2 entries matched exactly (see
   "Tested against the real `nflasha16_live.img`" above).
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
