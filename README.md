# RX100-Firmware

Notes, tooling, and research toward custom/modified firmware for the Sony
RX100V (DSC-RX100M5, CXD90014 SoC).

## ⚠️ Disclaimer

This is a personal research project. Nothing here is affiliated with,
endorsed by, or supported by Sony.

- **No warranty.** All content in this repository (docs, scripts, patches,
  firmware images) is provided "AS IS", with no warranty of any kind, express
  or implied, per the [LICENSE](LICENSE) (MIT).
- **You assume all risk.** Modifying, unpacking, repacking, or flashing
  firmware on a camera can render the device unusable ("bricked"), corrupt
  your settings/data, void your manufacturer warranty, and in rare cases pose
  a safety/fire risk (e.g. if firmware controls battery charging behavior).
  Nothing here is verified safe for any camera other than the specific
  device/firmware version it was tested against.
- **No liability.** The author(s) and contributors accept no responsibility
  or liability for any damage, data loss, bricked devices, warranty
  voidance, or any other direct, indirect, incidental, or consequential
  loss arising from the use, misuse, or inability to use anything in this
  repository. By using any file, instruction, or script here, you agree
  that you do so entirely at your own risk.
- **Reverse engineering / legal notice.** This repository documents
  independent reverse engineering of firmware for interoperability and
  research purposes. It does not include, and will not include, Sony's
  copyrighted firmware binaries or proprietary source code. You are
  responsible for ensuring your own use complies with your local laws, your
  camera's warranty terms, and Sony's end-user license agreement for any
  firmware/software involved.
- **Not for production/commercial use.** This is experimental, unfinished
  research tooling, not a supported product. Do not rely on it for anything
  safety-critical or professional.

If you are not comfortable with the possibility of permanently damaging your
camera, do not proceed past documentation/read-only steps (e.g. dumping
firmware for analysis is comparatively low-risk; flashing/patching firmware
back onto the camera is where real risk begins).

## Repository layout

- **[`backup/`](backup)** — How to extract a genuine, unmodified firmware
  package (`.dat`) from Sony's official update `.exe`, and how to
  rebuild/repack that same package as a safety net you can reflash if a
  custom firmware modification goes wrong. Includes a tested extraction
  script and step-by-step instructions.
- **[`dump-firmware/`](dump-firmware)** — How to pull live firmware images
  directly off a connected camera over USB (via `Sony-PMCA-RE`'s updater
  shell), and how to extract individual files (e.g. `libcamera.so`) out of
  the resulting partition dumps for offline analysis/reverse engineering.
- **[`docs/`](docs)** — Design notes and research write-ups, including the
  in-progress plan for a hybrid eye-AF firmware patch, its assumptions, open
  questions, and risk analysis.
