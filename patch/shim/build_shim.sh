#!/bin/bash
# 编译 libMagnetSearchPatch.dylib
# 用法: bash patch/shim/build_shim.sh [输出路径]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/libMagnetSearchPatch.dylib}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

OLD='_$s7SwiftUI4ViewP10MagnetShimE8onChange2of7initial_Qrqd___Sbyqd___qd__tctSQRd__lF'
NEW='_$s7SwiftUI4ViewPAAE8onChange2of7initial_Qrqd___Sbyqd___qd__tctSQRd__lF'

echo "== 1/2 编译 C 运行时转发 =="
clang -c -O2 -target x86_64-apple-macos12.0 "$HERE/rt_clean.c" -o "$TMP/rt.o"

echo "== 2/2 编译 Swift 补丁库 =="
xcrun swiftc -emit-library -module-name MagnetShim -target x86_64-apple-macos12.0 \
    -Xlinker -undefined -Xlinker dynamic_lookup \
    -Xlinker -alias -Xlinker "$OLD" -Xlinker "$NEW" \
    -Xlinker -alias -Xlinker "${OLD}QOMQ" -Xlinker "${NEW}QOMQ" \
    -Xlinker -install_name -Xlinker "@executable_path/../Frameworks/libMagnetSearchPatch.dylib" \
    -o "$OUT" "$HERE/shim.swift" "$HERE/slowpath.swift" "$TMP/rt.o"

echo "== 导出符号 =="
nm -gU "$OUT" | grep -E "onChange|swift_get|Ts5" || true
echo "✅ 生成：$OUT"
