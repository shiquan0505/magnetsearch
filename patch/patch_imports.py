#!/usr/bin/env python3
"""把 Mach-O chained-fixups 导入表中指定符号的 lib_ordinal 改成 flat lookup(0xFE)"""
import struct, sys

TARGETS = [
    '$s7SwiftUI4ViewPAAE8onChange2of7initial_Qrqd___Sbyqd___qd__tctSQRd__lF',
    '$s7SwiftUI4ViewPAAE8onChange2of7initial_Qrqd___Sbyqd___qd__tctSQRd__lFQOMQ',
    '$ss12_ArrayBufferV19_getElementSlowPathyyXlSiFyXl_Ts5',
    'swift_getOpaqueTypeConformance2',
    'swift_getTypeByMangledNameInContext2',
    'swift_getTypeByMangledNameInContextInMetadataState2',
]

def patch(path):
    d = bytearray(open(path,'rb').read())
    ncmds = struct.unpack('<I', d[16:20])[0]
    off, cf = 32, None
    for _ in range(ncmds):
        cmd, sz = struct.unpack('<II', d[off:off+8])
        if cmd == 0x80000034:
            cf = struct.unpack('<II', d[off+8:off+16])
        off += sz
    if not cf: sys.exit('no LC_DYLD_CHAINED_FIXUPS')
    base = cf[0]
    _, _, imp_off, sym_off, imp_count, imp_fmt, _ = struct.unpack('<7I', d[base:base+28])
    if imp_fmt != 1: sys.exit('unsupported imports_format=%d' % imp_fmt)
    done = []
    for i in range(imp_count):
        e = base + imp_off + i*4
        raw = struct.unpack('<I', d[e:e+4])[0]
        name_off = raw >> 9
        j = base + sym_off + name_off
        s = bytearray()
        while d[j]: s.append(d[j]); j += 1
        nm = s.decode()
        if nm.startswith('_') and nm[1:] in TARGETS:
            d[e:e+4] = struct.pack('<I', (raw & ~0xff) | 0xFE)  # flat lookup
            done.append((i, raw & 0xff, nm))
    open(path,'wb').write(d)
    for i, o, nm in done: print(f'  [import#{i}] lib_ord {o} -> 254  {nm}')
    print(f'patched {len(done)}/{len(TARGETS)}')

if __name__ == '__main__':
    patch(sys.argv[1] if len(sys.argv)>1 else 'MagnetSearch.app/Contents/MacOS/MagnetSearch')
