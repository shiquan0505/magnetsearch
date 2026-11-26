#!/usr/bin/env python3
"""给 Mach-O 追加一条 LC_LOAD_DYLIB 载入命令（利用 __TEXT 头部 padding，不移动任何 section）"""
import struct, sys

def align8(n): return (n + 7) & ~7

def add(path, install_name, dry=False):
    d = bytearray(open(path,'rb').read())
    magic, cputype, cpusub, filetype, ncmds, sizeofcmds, flags, reserved = struct.unpack('<IiiIIIII', d[:32])
    if magic != 0xfeedfacf: sys.exit('不是 MH_MAGIC_64')
    # 检查是否已经存在
    off = 32
    for _ in range(ncmds):
        cmd, sz = struct.unpack('<II', d[off:off+8])
        if cmd == 0xc:  # LC_LOAD_DYLIB
            n_off = struct.unpack('<I', d[off+8:off+12])[0]
            nm = d[off+n_off:off+sz].split(b'\0')[0].decode()
            if nm == install_name:
                print('已存在 LC_LOAD_DYLIB:', nm); return
        off += sz
    hdr_end = 32 + sizeofcmds
    # 找到第一个 section 的 file offset，作为可用上界
    limit = None
    off = 32
    for _ in range(ncmds):
        cmd, sz = struct.unpack('<II', d[off:off+8])
        if cmd == 0x19:  # LC_SEGMENT_64
            nsects = struct.unpack('<I', d[off+64:off+68])[0]
            so = off + 72
            for _s in range(nsects):
                if limit is None or so < limit:
                    pass
                foff = struct.unpack('<I', d[so+48:so+52])[0]
                if foff and (limit is None or foff < limit): limit = foff
                so += 80
        off += sz
    name = install_name.encode() + b'\0'
    lc_size = align8(24 + len(name))
    need = hdr_end + lc_size
    print(f'header_end={hdr_end} 第一个 section offset={limit} 需要={need}')
    if limit is None or need > limit:
        sys.exit('头部 padding 不足，需要重排 section')
    lc = struct.pack('<IIIIII', 0xc, lc_size, 24, 2, 0x10000, 0x10000) + name
    lc = lc.ljust(lc_size, b'\0')
    if dry: return
    d[hdr_end:need] = lc
    d[16:20] = struct.pack('<I', ncmds + 1)
    d[20:24] = struct.pack('<I', sizeofcmds + lc_size)
    open(path,'wb').write(d)
    print(f'已追加 LC_LOAD_DYLIB {install_name} ({lc_size} 字节)')

if __name__ == '__main__':
    add(sys.argv[1], sys.argv[2] if len(sys.argv)>2 else '@executable_path/../Frameworks/libMagnetSearchPatch.dylib')
