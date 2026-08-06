# Creating a Backup Firmware Package (RX100M5, CXD90014)

These steps produce a flashable `.dat` package containing your camera's
currently-installed firmware, for use as a rollback/backup if a future flash
goes wrong. Verified working end-to-end on RX100M5 (August 2026).

## Prerequisites

- [fwtool.py](https://github.com/JEF1056/fwtool.py) — packs/unpacks Sony `.dat`/`.fdat` firmware images (fork with LZPT write support and performance fixes; see [`../patch/README.md`](../patch/README.md)).
- [Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE) — talks to the camera over USB to read/write firmware.
- The genuine Sony updater `.exe` for your camera's **currently installed** firmware version (e.g. `Update_DSCRX100M5V200.exe`), or a `.dat` already extracted from it. [Download link](https://www.sony.com/electronics/support/compact-cameras-dsc-rx-series/dsc-rx100m5/downloads/W0011102)

> Tested against a installer .exe with SHA-256
> `105004cce589cf201a3b05d466b12709c9ac3203de9ca5a2269a8fb9d87aca9c`
> (`Update_DSCRX100M5V200.exe`, firmware v2.00). Reproduce with:
> `shasum -a 256 Update_DSCRX100M5V200.exe`

## 1. Extract the genuine firmware.dat from the updater .exe

```
python3 fwtool.py unpack -f Update_DSCRX100M5V200.exe -o unpacked/
```

This produces `unpacked/firmware.dat`, `firmware.fdat`, `firmware.tar`,
`config.yaml`, and `updater.img` (the filesystem/`fs` partition).

### Fallback: `Exception: Unknown exe file`

Some installer SFX wrappers aren't recognized by `fwtool.py`'s `lzh` parser
(no `-lh0-` method string found where expected). Work around it with
[`extract_dat_from_exe.py`](extract_dat_from_exe.py) (included in this
folder), which locates the `.dat` magic bytes directly and slices the file
from that offset to EOF, producing a `firmware.dat` you can unpack directly
instead of the `.exe`:

```
cd [path-to-RX100V-Firmware/backup]
python3 extract_dat_from_exe.py Update_DSCRX100M5V200.exe unpacked/
cd [path-to-fwtool-repo]
python3 fwtool.py unpack -f unpacked/firmware.dat -o unpacked/
```

The second command produces the same `firmware.tar`/`config.yaml`/`updater.img`
outputs as the normal path.

## 2. Keep the config and `updater.img`

`unpacked/config.yaml` records the exact `crypterName`, USB descriptors,
`model`, `region`, and `version` needed to rebuild a valid package.
`unpacked/updater.img` is the real `fs` partition content — **this must be
included when repacking**, or the camera will reject the write with an
undocumented `CMD_WRITE_FIRM` error (`0x1074`) even though earlier checks
(`checkGuard`, version query) succeed.

## 3. Build the backup/rollback package

Copy `config.yaml` and bump only the `fdat.version` field to a value **higher**
than whatever version is currently installed on the camera (the device's
`checkGuard` rejects any version that isn't strictly greater than the
installed one). The firmware content itself can remain the original,
unmodified `firmware.tar` — only the version string needs to increase.

```
python3 fwtool.py pack \
  -c config_backup.yaml \
  -u unpacked/updater.img \
  -f unpacked/firmware.tar \
  -o backup_pack
```

This produces `backup_pack/firmware_packed.dat` — a package whose real
content matches your original firmware exactly, just labeled with a higher
version number so the camera accepts writing it back.

## 4. Flash the backup package (recovery step)

```
cd sony-pmca
sudo python3 pmca-console.py firmware -f /path/to/backup_pack/firmware_packed.dat
```

Follow the on-camera mode-switch prompts. The camera will report "Updating
from version X to version Y", write the firmware, and print "Done" on
success.

## Notes

- Power-cycling the camera safely recovers it if a flash attempt fails partway
  (before "Done" is printed) — no data is committed to flash until the write
  completes.
- Keep a copy of the genuine `firmware.dat`/`firmware.tar`/`updater.img`
  extracted in step 1 indefinitely — they're your ground-truth backup and the
  basis for rebuilding a rollback package after any future experimental flash.
