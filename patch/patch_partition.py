#!/usr/bin/env python3
"""Patch a single file inside a dumped nflasha16-style partition image, in place.

Structure confirmed directly against a real nflasha16 dump (2026-08-05):

    nflasha16 (raw partition)
     -> LZPT (TPZL magic, fwtool.archive.lzpt)
         -> ext2 filesystem (763 entries)
             -> target file's raw bytes directly (e.g. /lib/libcamera.so is
                stored uncompressed - NOT further wrapped in gzip/cramfs/etc;
                some *other* files in this ext2 image are gzip-compressed,
                but the target file used by this project is not)

fwtool.py has no ext2 writer, and writing one (block/group descriptor/bitmap
management) is a much bigger job than this patch needs. Instead, this script
does an in-place byte patch: it locates the target file's data blocks via the
same inode/block-pointer resolution algorithm as fwtool's own
fwtool/archive/ext2.py (imported directly, not reimplemented independently),
overwrites those bytes with the new content, and requires the new content be
*exactly* the same length as the original file. This is sufficient for
instruction/constant-level binary patches that don't change a file's size,
which is the kind of patch this project produces. If a future patch needs to
change a file's size, this script will refuse with a clear error rather than
silently corrupting the filesystem - that case needs real ext2 block
allocation, which is out of scope here.

Usage:
    python3 patch_partition.py <partition.img> <target_path> <new_content_file> <out_partition.img>

Example:
    python3 patch_partition.py nflasha16.img /lib/libcamera.so libcamera_patched.so nflasha16_patched.img
"""
import io
import sys

FWTOOL_PATH = '/Users/jfan/Documents/Github/fwtool.py'
sys.path.insert(0, FWTOOL_PATH)
from fwtool.archive import lzpt
from fwtool.archive import ext2 as ext2mod
from fwtool.util import parse32le


def _resolveBlocks(file, header, blockSize, inodeTables, inodeNum):
    """Returns (inode, [blockPtr, ...]) for inodeNum, mirroring ext2.py's readInode block-pointer resolution."""
    inode = ext2mod.Ext2Inode.unpack(file, inodeTables[(inodeNum - 1) // header.inodesPerGroup] * blockSize + ((inodeNum - 1) % header.inodesPerGroup) * header.inodeSize)

    contents = inode.blocks
    ptrs = []
    for i in range(15, 11, -1):
        contents = contents[:i * 4]
        for ptr in ptrs[i:]:
            if ptr != 0:
                file.seek(ptr * blockSize)
                contents += file.read(blockSize)
        ptrs = [parse32le(contents[j:j + 4]) for j in range(0, len(contents), 4)]

    return inode, ptrs


def _findInode(file, header, blockSize, inodeTables, targetPath):
    """Walks the directory tree from the root inode (always #2) to find targetPath's inode number."""
    def walk(inodeNum, path):
        inode, ptrs = _resolveBlocks(file, header, blockSize, inodeTables, inodeNum)
        if path == targetPath:
            return inodeNum, inode

        from stat import S_ISDIR
        if not S_ISDIR(inode.mode):
            return None

        read = 0
        buf = b''
        for ptr in ptrs:
            if read >= inode.size:
                break
            if ptr != 0:
                file.seek(ptr * blockSize)
                buf += file.read(blockSize)
            else:
                buf += b'\0' * blockSize
            read += blockSize
        buf = buf[:inode.size]

        pos = 0
        while pos < len(buf):
            entry = ext2mod.Ext2DirEntry.unpack(buf, pos)
            nameStart = pos + ext2mod.Ext2DirEntry.size
            name = buf[nameStart:nameStart + entry.nameSize].decode('ascii')
            if name not in ('.', '..') and entry.inode != 0:
                result = walk(entry.inode, path.rstrip('/') + '/' + name)
                if result is not None:
                    return result
            pos += entry.size

        return None

    return walk(2, '')


def patchExt2File(ext2Bytes, targetPath, newContent):
    """Returns a copy of ext2Bytes with targetPath's data overwritten in place."""
    file = io.BytesIO(ext2Bytes)

    header = ext2mod.Ext2Header.unpack(file)
    if header.magic != ext2mod.ext2HeaderMagic:
        raise Exception('Not an ext2 image')
    blockSize = 1024 << header.blockSize
    bdgOffset = max(blockSize, 2048)
    numBlockGroups = (header.blocksCount - 1) // header.blocksPerGroup + 1
    inodeTables = [ext2mod.Ext2Bgd.unpack(file, bdgOffset + i * ext2mod.Ext2Bgd.size).inodeTableBlock for i in range(numBlockGroups)]

    result = _findInode(file, header, blockSize, inodeTables, targetPath)
    if result is None:
        raise Exception('%s not found in ext2 image' % targetPath)
    inodeNum, inode = result

    if len(newContent) != inode.size:
        raise Exception(
            'New content is %d bytes but %s is %d bytes on disk - this script '
            'only supports same-size in-place patches (no ext2 writer/block '
            'allocator implemented). Pad or trim the patch to match exactly.'
            % (len(newContent), targetPath, inode.size)
        )

    _, ptrs = _resolveBlocks(file, header, blockSize, inodeTables, inodeNum)
    numDataBlocks = (inode.size + blockSize - 1) // blockSize

    out = bytearray(ext2Bytes)
    for i in range(numDataBlocks):
        ptr = ptrs[i]
        if ptr == 0:
            raise Exception('%s has a sparse hole in its data blocks - in-place patch not supported' % targetPath)
        chunk = newContent[i * blockSize:(i + 1) * blockSize]
        offset = ptr * blockSize
        out[offset:offset + len(chunk)] = chunk

    return bytes(out)


def patchPartition(partitionImg, targetPath, newContent):
    """Decodes an LZPT partition image, patches targetPath in place, and re-encodes it."""
    files = list(lzpt.readLzpt(io.BytesIO(partitionImg)))
    ext2Bytes = files[0].contents.read()

    patched = patchExt2File(ext2Bytes, targetPath, newContent)

    outFile = io.BytesIO()
    lzpt.writeLzpt([files[0]._replace(contents=io.BytesIO(patched))], outFile)
    return outFile.getvalue()


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    partitionPath, targetPath, newContentPath, outPath = sys.argv[1:5]

    with open(partitionPath, 'rb') as f:
        partitionImg = f.read()
    with open(newContentPath, 'rb') as f:
        newContent = f.read()

    patched = patchPartition(partitionImg, targetPath, newContent)

    with open(outPath, 'wb') as f:
        f.write(patched)
    print('Wrote %s (%d bytes)' % (outPath, len(patched)))


if __name__ == '__main__':
    main()
