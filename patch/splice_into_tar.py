#!/usr/bin/env python3
"""Replace one member's contents inside firmware.tar, copying everything else through unchanged.

This is the last step of the patch pipeline: patch_partition.py produces a new
nflasha16 partition image; this script splices those bytes into the outer
firmware.tar in place of the original nflasha16 member, preserving every other
member's bytes and metadata (mtime/mode/uid/gid) exactly. fwtool.py has no tar
writer, so this uses Python's stdlib tarfile module directly - tar is a
standard, well-understood format, so this carries much less risk than the
proprietary LZPT/ext2 layers.

Usage:
    python3 splice_into_tar.py <firmware.tar> <member_name> <new_content_file> <out_firmware.tar>

Example:
    python3 splice_into_tar.py firmware.tar nflasha16 nflasha16_patched.img firmware_patched.tar
"""
import sys
import tarfile
import io


def spliceIntoTar(inTarPath, memberName, newContent, outTarPath):
    with tarfile.open(inTarPath, 'r') as inTar:
        members = inTar.getmembers()
        if not any(m.name == memberName for m in members):
            raise Exception('%s not found in %s' % (memberName, inTarPath))

        with tarfile.open(outTarPath, 'w') as outTar:
            for member in members:
                if member.name == memberName:
                    newMember = tarfile.TarInfo(name=member.name)
                    newMember.size = len(newContent)
                    newMember.mtime = member.mtime
                    newMember.mode = member.mode
                    newMember.uid = member.uid
                    newMember.gid = member.gid
                    newMember.uname = member.uname
                    newMember.gname = member.gname
                    newMember.type = member.type
                    outTar.addfile(newMember, io.BytesIO(newContent))
                else:
                    fileObj = inTar.extractfile(member) if member.isfile() else None
                    outTar.addfile(member, fileobj=fileObj)


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    inTarPath, memberName, newContentPath, outTarPath = sys.argv[1:5]

    with open(newContentPath, 'rb') as f:
        newContent = f.read()

    spliceIntoTar(inTarPath, memberName, newContent, outTarPath)
    print('Wrote %s with %s replaced (%d bytes)' % (outTarPath, memberName, len(newContent)))


if __name__ == '__main__':
    main()
