#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关闭上游 app 的 24 小时试用 / 激活码校验（可选步骤）

改三处函数入口（x86_64，upstream build 1.0 的偏移）：
  isActivated.getter          -> mov al,1; ret         恒真
  remainingTrialTime.getter   -> mov eax,INT_MAX; ret  剩余时间恒为正
  isTrialExpired()            -> xor eax,eax; ret      恒假

用法: python3 patch_license.py <Mach-O 路径>
换版本后偏移会变，用下面的命令重新定位：
  nm -n <Mach-O> | grep -E "isActivatedSbvg|remainingTrialTimeSivg|isTrialExpiredSbyF"
"""
import shutil, struct, subprocess, sys

# 虚拟地址：x86_64 上 __TEXT 的 vmaddr 是 0x100000000、fileoff 是 0
TEXT_VMADDR = 0x100000000

PATCHES = [
    (0x100013380, bytes([0xB0, 0x01, 0xC3]), 'isActivated.getter        -> true'),
    (0x1000133E0, bytes([0xB8, 0xFF, 0xFF, 0xFF, 0x7F, 0xC3]), 'remainingTrialTime.getter -> INT_MAX'),
    (0x100013C40, bytes([0x31, 0xC0, 0xC3]), 'isTrialExpired()          -> false'),
]


def locate(path):
    """用 nm 校验三个符号是否还在预期偏移上"""
    try:
        out = subprocess.run(['nm', '-n', path], capture_output=True, text=True).stdout
    except Exception:
        return
    want = {
        'isActivatedSbvg': 0x100013380,
        'remainingTrialTimeSivg': 0x1000133E0,
        'isTrialExpiredSbyF': 0x100013C40,
    }
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        addr_s, typ, name = parts[0], parts[1], parts[2]
        if typ not in ('t', 'T'):          # 只认函数符号，跳过 method descriptor
            continue
        for key, addr in want.items():
            if name.endswith(key) and 'LicenseManager' in name:
                got = int(addr_s, 16)
                flag = 'OK' if got == addr else f'!! 预期 {hex(addr)}，偏移已变！'
                print(f'  {key:26} {hex(got):>12}  {flag}')


def main():
    path = sys.argv[1]
    shutil.copy2(path, path + '.license.bak')
    d = bytearray(open(path, 'rb').read())
    print('符号定位检查：')
    locate(path)
    for va, code, desc in PATCHES:
        o = va - TEXT_VMADDR
        origin = d[o:o + len(code)].hex()
        d[o:o + len(code)] = code
        print(f'{hex(va)}  {origin} => {code.hex()}   {desc}')
    open(path, 'wb').write(d)
    print('\n完成。原文件备份在', path + '.license.bak')
    print('记得重新签名：codesign --force --deep --sign - <App.app>')


if __name__ == '__main__':
    main()
