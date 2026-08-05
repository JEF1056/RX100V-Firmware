#!/usr/bin/env python3
"""Fallback extractor for updater .exe files with an SFX wrapper fwtool.py's
lzh parser doesn't recognize (raises 'Unknown exe file' on unpack).

Locates the Sony .dat magic bytes directly and slices the file from that
offset to EOF, producing a firmware.dat that fwtool.py unpack can consume
directly, bypassing unpackInstaller() entirely.

Usage:
    python3 extract_dat_from_exe.py <updater.exe> <outDir>
"""
import shutil
import sys
import os

DAT_MAGIC = b'\x89UFU\r\n\x1a\n'


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    exeFile, outDir = sys.argv[1], sys.argv[2]

    os.makedirs(outDir, exist_ok=True)
    with open(exeFile, 'rb') as src:
        data = src.read()
        offset = data.find(DAT_MAGIC)
        if offset < 0:
            sys.exit('Error: .dat magic bytes not found in %s' % exeFile)
        src.seek(offset)
        outPath = os.path.join(outDir, 'firmware.dat')
        with open(outPath, 'wb') as dst:
            shutil.copyfileobj(src, dst)

    print('Extracted %s (offset 0x%x) -> %s' % (exeFile, offset, outPath))
    print('Next: python3 fwtool.py unpack -f %s -o %s' % (outPath, outDir))


if __name__ == '__main__':
    main()
