# nflash partition layout (RX100V / DSC-RX100M5, CXD90014)

Source: `0300_partconf/partinf.conf` inside the genuine firmware's
`firmware.tar` (see [`../backup/`](../backup) for how to extract this
yourself). Reproduced here for quick reference when deciding which
partition(s) to `pull` — see [README.md](README.md) for the dump process
itself.

| Device | Size | Group | Contents |
|---|---|---|---|
| `/dev/nflasha1` | 6MB | System | SYSTEM (Updater) |
| `/dev/nflasha2` | 13MB | System | SYSTEM (Setting) |
| `/dev/nflasha3` | 20MB | System | SYSTEM (Main) |
| `/dev/nflasha4` | 20MB | System | MPR Flash Backup (internal, not user-accessible) |
| `/dev/nflasha5` | 60MB | System | Warm Boot Image |
| `/dev/nflasha6` | 5MB | System | Jiritsu |
| `/dev/nflasha7` | 4MB | System | Rootfs |
| `/dev/nflasha8`, `9` | 0MB | System | reserved |
| `/dev/nflasha10` | 10MB | Work | Work |
| `/dev/nflasha11` | 6MB | Work | Log |
| `/dev/nflasha12`–`14` | 0MB | Work | reserved |
| `/dev/nflasha15` | 100MB | User | **User (cramfs)** — contains `arbiter.so`/`libBizFw*.so` and the ramdisk/`init.rc` |
| `/dev/nflasha16` | 82.75MB | User | **Android (system)** — contains `libcamera.so` and most Android userspace |
| `/dev/nflasha17` | 99.75MB | User | Android (data) |
| `/dev/nflasha18` | 1MB | User | LensData |
| `/dev/nflasha19`–`22` | 0MB | User | reserved |
| `/dev/nflasha23` | 2MB | Customer | PMBP |
| `/dev/nflasha24` | 10MB | Customer | Storage |
| `/dev/nflasha25` | 19MB | Customer | BGM (MP3) |
| `/dev/nflasha26` | 0MB | Customer | BGM (AC3) |
| `/dev/nflasha27`–`30` | 0MB | Customer | reserved |

There is also a bare `/dev/nflasha` device node representing the entire raw
flash chip (not a partition-specific alias) — `pull /dev/nflasha <path>`
dumps everything at once (~500MB), useful as a full backup/rollback baseline
but slow and mostly unnecessary if you only need a specific partition.

## Which partition for which RE task

- **`libcamera.so` / Camera HAL work** (see
  [`../docs/rx100m5_hybrid_eyeaf_plan.md`](../docs/rx100m5_hybrid_eyeaf_plan.md)):
  `nflasha16`.
- **Stock shooting-UI binaries** (`arbiter.so`, `libBizFw*.so`) or boot
  process (`init.rc`): `nflasha15`.
- **Boot loader/boot ROM** (not part of the nflash chip at all): use the
  shell's dedicated `bootloader`/`bootrom` commands instead of `pull`.

## Partition image format

Each `nflasha15`/`nflasha16`-style dump is a nested container: a compressed
blob (TPZL/LZPT) that decompresses to a cramfs, which can itself contain
further nested single-entry archives around the file you actually want. See
[README.md](README.md#extracting-a-specific-file-out-of-a-partition-dump) and
[`extract_from_partition.py`](extract_from_partition.py) for unwrapping these
layers automatically.
