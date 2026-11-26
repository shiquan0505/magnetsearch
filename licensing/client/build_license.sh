#!/bin/bash
# 构建授权注入库 + 重新构建带钩子的 libMagnetSearchPatch.dylib
#
#   bash licensing/client/build_license.sh
#
# 产物（默认 licensing/build/）：
#   libMagnetSearchLicense.dylib   授权门禁（试用/买断/收银台）
#   libMagnetSearchPatch.dylib     兼容补丁库 + 授权钩子（链接上面那个）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"          # MagnetSearch-macOS/
LIC_ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$LIC_ROOT/build}"
TARGET="${MS_TARGET:-x86_64-apple-macos12.0}"

mkdir -p "$OUT"

echo "== 0/4 生成客户端配置（公钥/价格/服务端地址）=="
if [ -n "${MS_LICENSE_CONFIG:-}" ]; then
    echo "   使用配置：$MS_LICENSE_CONFIG"
    python3 "$LIC_ROOT/tools/gen_client_config.py" "$MS_LICENSE_CONFIG"
else
    echo "   使用配置：$LIC_ROOT/config.json（生产）"
    python3 "$LIC_ROOT/tools/gen_client_config.py"
fi

echo "== 1/4 编译 C 入口 =="
mkdir -p "$OUT/obj"
clang -c -O2 -target "$TARGET" "$HERE/entry.c" -o "$OUT/obj/entry.o"
clang -c -O2 -target "$TARGET" "$HERE/license_hook.c" -o "$OUT/obj/license_hook.o"

echo "== 2/4 编译授权库 libMagnetSearchLicense.dylib =="
xcrun swiftc -emit-library -O -module-name MagnetSearchLicense \
    -target "$TARGET" \
    -Xlinker -install_name -Xlinker @executable_path/../Frameworks/libMagnetSearchLicense.dylib \
    -Xlinker -undefined -Xlinker dynamic_lookup \
    -o "$OUT/libMagnetSearchLicense.dylib" \
    "$HERE/LicenseConfig.generated.swift" "$HERE/LicenseToken.swift" "$HERE/DeviceIdentity.swift" \
    "$HERE/LocalState.swift" "$HERE/LicenseAPI.swift" "$HERE/PaywallWindow.swift" \
    "$HERE/LicenseGate.swift" "$HERE/PaywallQR.swift" "$HERE/SettingsLicense.swift" "$HERE/entry.swift" "$OUT/obj/entry.o" \
    -framework AppKit -framework CoreImage -framework CryptoKit -framework IOKit -framework Vision

echo "== 3/4 重新编译兼容补丁库（链接授权库）=="
"$LIC_ROOT/../patch/shim/build_shim.sh" "$OUT/libMagnetSearchPatch.dylib.tmp" >/dev/null
python3 - "$OUT/libMagnetSearchPatch.dylib.tmp" "$OUT/libMagnetSearchPatch.dylib" <<'PY'
import struct, subprocess, sys
src, dst = sys.argv[1], sys.argv[2]
# 用 ld 重新链接不方便（要保留 -alias），这里选择“追加依赖”：
# 直接在 dylib 里增加一条 LC_LOAD_DYLIB，指向授权库；并追加一个 constructor 调用。
data = bytearray(open(src, 'rb').read())
magic, cputype, cpusub, filetype, ncmds, sizeofcmds, flags, res = struct.unpack('<IiiIIIII', data[:32])
assert magic == 0xfeedfacf, 'not MH_MAGIC_64'

# 找 __TEXT 第一个 section 的 file offset 作为 header padding 上界
limit = None
off = 32
for _ in range(ncmds):
    cmd, sz = struct.unpack('<II', data[off:off+8])
    if cmd == 0x19:  # LC_SEGMENT_64
        nsects = struct.unpack('<I', data[off+64:off+68])[0]
        so = off + 72
        for _s in range(nsects):
            foff = struct.unpack('<I', data[so+48:so+52])[0]
            if foff and (limit is None or foff < limit):
                limit = foff
            so += 80
    off += sz

name = b'@executable_path/../Frameworks/libMagnetSearchLicense.dylib\0'
lc_size = (24 + len(name) + 7) & ~7
need = 32 + sizeofcmds + lc_size
have = limit - (32 + sizeofcmds)
if have < lc_size:
    sys.exit('dylib 头部 padding 不足（需要 %d，只有 %d）' % (lc_size, have))
lc = struct.pack('<IIIIII', 0xc, lc_size, 24, 2, 0x10000, 0x10000) + name
lc = lc.ljust(lc_size, b'\0')
data[32 + sizeofcmds:need] = lc
data[16:20] = struct.pack('<I', ncmds + 1)
data[20:24] = struct.pack('<I', sizeofcmds + lc_size)
open(dst, 'wb').write(data)
print('   已给补丁库追加 LC_LOAD_DYLIB → 授权库（padding 用 %d/%d 字节）' % (lc_size, have))
PY
rm -f "$OUT/libMagnetSearchPatch.dylib.tmp"

# 给补丁库一个 @rpath 之外的稳定 install_name（保持原样：@executable_path/../Frameworks/...）
chmod +x "$OUT"/*.dylib
echo "== 4/4 产物 =="
for f in "$OUT"/*.dylib; do
    echo "   $f"
    otool -L "$f" | sed -n '2,8p' | sed 's/^/       /'
done
echo
echo "下一步：bash licensing/client/apply_to_app.sh <MagnetSearch.app>"
