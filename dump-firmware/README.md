# Dumping firmware from the camera

This documents how to pull live firmware images directly off a connected
RX100V/RX100M5 (or similar CXD90014-based Sony camera) for offline analysis
(e.g. in Ghidra), using [Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE).

This is different from [`../backup/`](../backup), which extracts firmware
*packages* (`.dat`/`.exe`) that Sony ships for updates. The process here reads
back what's actually programmed on the camera's flash chip right now.

## Prerequisites

- `Sony-PMCA-RE` cloned, with `pip install -r requirements.txt`.
- `brew install libusb` (fixes pyusb's `NoBackendError` on macOS).
- The camera connected via USB, powered on.
- **Apple Silicon Macs only**: libusb cannot claim the camera's Mass Storage
  interface on Apple Silicon — macOS routes it through a DriverKit system
  extension that blocks `claim_interface` even as root. You need a
  community/third-party **Apple-Silicon-compatible native MSC driver** for
  Sony cameras installed (shows up as `OS-X-MSC` in pmca-console's driver
  list). Without it, `updatershell`/`serviceshell`/`info` all fail with
  `usb.core.USBError: [Errno None] Other error` at `libusb_claim_interface`,
  no matter what else you try (replugging, sudo, killing Image Capture).
  Intel Macs, Windows (Zadig driver), or Linux work without this driver.

## Entering the updater shell

```
cd Sony-PMCA-RE
sudo python3 pmca-console.py updatershell
```

- Requires `sudo` for raw USB device access on macOS (enter your password
  when prompted — this must be done manually, not scripted).
- The camera will reboot into its firmware update USB mode. On success you'll
  see something like `Using drivers OS-X-MSC, libusb-MTP, libusb-vendor-specific`
  followed by a `Welcome to platform shell` prompt.

## Commands available in the shell

Once at the `platform shell` prompt (from `pmca/platform/__init__.py`'s
`CameraShell`):

| Command | Description |
|---|---|
| `pull <REMOTE> [<LOCAL>]` | Copy a file/device node from the camera to your computer |
| `push <LOCAL> <REMOTE>` | Copy a file from your computer to the camera |
| `bootloader [<OUTDIR>]` | Dump the boot loader stages (boot1-5) |
| `bootrom [<OUTDIR>]` | Dump the boot ROM |
| `info` | Print device info |
| `bk r/w/patch/s/lock/unlock` | Read/write backup (settings) properties |

`pull` and `push` accept raw device paths like `/dev/nflasha16`, since the
platform shell has direct block-device access — not just a mounted
filesystem. Run `help` in the shell to see what's available for your
specific device.

Run pull/push commands **one at a time** and wait for each to reach 100%
before issuing the next; the interactive shell doesn't reliably queue
concurrent transfers.

## Which partitions to pull

See [partition layout notes](../docs/rx100m5_hybrid_eyeaf_plan.md) for the
full breakdown. For reverse-engineering `libcamera.so`-related behavior:

```
pull /dev/nflasha16 nflasha16_live.img   # Android system partition, has libcamera.so (~83MB)
pull /dev/nflasha15 nflasha15_live.img   # User cramfs, has arbiter.so/libBizFw*.so (~100MB)
pull /dev/nflasha nflasha_full_backup.img  # entire flash chip, useful as a full backup (~500MB)
```

Dumped files will be owned by `root` (since the shell itself runs under
`sudo`); fix ownership afterward with:

```
sudo chown $(id -un):staff nflasha15_live.img nflasha16_live.img nflasha_full_backup.img
```

## Extracting a specific file out of a partition dump

Partition images are nested containers — typically a compressed blob
(TPZL/LZPT) that decompresses to a cramfs, which can itself contain further
nested single-entry archives around the file you actually want. Rather than
manually unwrapping each layer, [`extract_from_partition.py`](extract_from_partition.py)
reuses `fwtool.py`'s own archive-reading code (`archive.isArchive()` /
`archive.readArchive()`), which already understands all of these formats.

**Requirements**: a local clone of `fwtool.py`. The script hardcodes its path
via `FWTOOL_PATH` at the top of the file (defaults to
`/Users/jfan/Documents/Github/fwtool.py`) — edit that constant if your clone
lives elsewhere.

**Usage**:

```
python3 extract_from_partition.py <partition.img> <target_filename_suffix> <outFile>
```

- `<partition.img>` — a partition dump from `pull` above (e.g.
  `nflasha16_live.img`).
- `<target_filename_suffix>` — a suffix to match against entry paths inside
  the (possibly deeply nested) archive, e.g. `libcamera.so`. Matching is a
  simple `str.endswith()`, so any file ending in that suffix is extracted —
  use a more specific suffix (e.g. `lib/libcamera.so`) if there's a risk of
  ambiguity.
- `<outFile>` — where to write the extracted file.

Example, extracting `libcamera.so` out of the Android system partition:

```
python3 extract_from_partition.py nflasha16_live.img libcamera.so libcamera_live.so
```

The script recurses depth-first through nested archives, printing each
container's entry count as it goes, and exits with an error if no entry
matching the suffix is found anywhere in the tree.

## Verifying a dump is genuine

To confirm a `pull`-based dump is a faithful readback of what's on the
camera (and not, say, an artifact of the extraction process), compare it
against the equivalent file unpacked from a known-good `.dat`/`.exe` package
(see [`../backup/`](../backup)) via `shasum -a 256`. A byte-for-byte match
confirms the live dump is accurate.
