#!/usr/bin/env python3
"""Recursively extract a named file from a dumped nflash partition image.

Sony's flash partitions are nested containers: a raw partition dump is
typically a compressed blob (TPZL/LZPT) that decompresses to a cramfs, which
may itself contain further nested single-entry archives around the actual
file you want (e.g. libcamera.so, arbiter.so). This walks that structure
using fwtool.py's own archive.isArchive()/readArchive(), which already
understands all of these container formats.

Usage:
    python3 extract_from_partition.py <partition.img> <target_filename_suffix> <outFile>

Example:
    python3 extract_from_partition.py nflasha16_live.img libcamera.so libcamera_live.so
"""
import io
import sys
from stat import S_ISREG

FWTOOL_PATH = '/Users/jfan/Documents/Github/fwtool.py'
sys.path.insert(0, FWTOOL_PATH)
from fwtool import archive


def recurse(f, targetSuffix, outPath, depth=0):
    if not archive.isArchive(f):
        return False
    entries = list(archive.readArchive(f))
    print('%sarchive with %d entries' % ('  ' * depth, len(entries)), flush=True)
    for e in entries:
        if not S_ISREG(e.mode) or e.contents is None:
            continue
        if e.path.endswith(targetSuffix):
            data = e.contents.read()
            print('%s- %r size=%d' % ('  ' * depth, e.path, len(data)), flush=True)
            with open(outPath, 'wb') as out:
                out.write(data)
            print('%s  ** extracted %s **' % ('  ' * depth, targetSuffix), flush=True)
            return True
        data = e.contents.read()
        if recurse(io.BytesIO(data), targetSuffix, outPath, depth + 1):
            return True
    return False


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    partitionImg, targetSuffix, outPath = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(partitionImg, 'rb') as f:
        if not recurse(f, targetSuffix, outPath):
            sys.exit('Error: %s not found inside %s' % (targetSuffix, partitionImg))


if __name__ == '__main__':
    main()
