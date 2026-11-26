#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Mach-O 的 LC_BUILD_VERSION.minos 改成指定版本（等长字段原地改）

用法: python3 patch_minos.py <Mach-O 路径> [目标版本=12.0]
"""
import struct, sys

def ver(v):
    a = (v.split('.') + ['0', '0'])[:3]
    return (int(a[0]) << 16) | (int(a[1]) << 8) | int(a[2])

def main():
    path = sys.argv[1]
    target = ver(sys.argv[2] if len(sys.argv) > 2 else '12.0')
    d = bytearray(open(path, 'rb').read())
    if struct.unpack('<I', d[:4])[0] != 0xFEEDFACF:
        sys.exit('不是 64 位 Mach-O（MH_MAGIC_64）')
    ncmds = struct.unpack('<I', d[16:20])[0]
    off, found = 32, 0
    for _ in range(ncmds):
        cmd, size = struct.unpack('<II', d[off:off + 8])
        if cmd == 0x32:                      # LC_BUILD_VERSION
            platform, minos, sdk = struct.unpack('<III', d[off + 8:off + 20])
            old = '%d.%d' % (minos >> 16, (minos >> 8) & 0xFF)
            d[off + 12:off + 16] = struct.pack('<I', target)
            print(f'LC_BUILD_VERSION @ {hex(off)}: minos {old} -> '
                  f'{target >> 16}.{(target >> 8) & 0xFF}  (sdk {sdk >> 16}.{(sdk >> 8) & 0xFF})')
            found += 1
        elif cmd == 0x24:                    # LC_VERSION_MIN_MACOSX（老式命令）
            v = struct.unpack('<I', d[off + 8:off + 12])[0]
            d[off + 8:off + 12] = struct.pack('<I', target)
            print(f'LC_VERSION_MIN_MACOSX @ {hex(off)}: {v >> 16}.{(v >> 8) & 0xFF} -> '
                  f'{target >> 16}.{(target >> 8) & 0xFF}')
            found += 1
        off += size
    if not found:
        sys.exit('没找到 LC_BUILD_VERSION / LC_VERSION_MIN_MACOSX')
    open(path, 'wb').write(d)

if __name__ == '__main__':
    main()
