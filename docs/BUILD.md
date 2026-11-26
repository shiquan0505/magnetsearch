# 从零复现构建

前置：Xcode Command Line Tools（`swiftc`、`clang`、`codesign`、`pkgbuild`、`hdiutil`）。
需要一个**上游 `MagnetSearch.app`**（本仓库不提供）。

---

## 0. 目录约定

```bash
export APP=/path/to/MagnetSearch.app
```

---

## 1. 编译补丁库

```bash
bash patch/shim/build_shim.sh ./libMagnetSearchPatch.dylib
```

脚本做的事：

```bash
OLD='_$s7SwiftUI4ViewP10MagnetShimE8onChange2of7initial_Qrqd___Sbyqd___qd__tctSQRd__lF'
NEW='_$s7SwiftUI4ViewPAAE8onChange2of7initial_Qrqd___Sbyqd___qd__tctSQRd__lF'

clang -c -target x86_64-apple-macos12.0 patch/shim/rt_clean.c -o /tmp/rt.o
swiftc -emit-library -module-name MagnetShim -target x86_64-apple-macos12.0 \
  -Xlinker -undefined -Xlinker dynamic_lookup \
  -Xlinker -alias -Xlinker "$OLD" -Xlinker "$NEW" \
  -Xlinker -alias -Xlinker "${OLD}QOMQ" -Xlinker "${NEW}QOMQ" \
  -Xlinker -install_name -Xlinker "@executable_path/../Frameworks/libMagnetSearchPatch.dylib" \
  -o libMagnetSearchPatch.dylib patch/shim/shim.swift patch/shim/slowpath.swift /tmp/rt.o
```

验证导出符号：

```bash
nm -gU libMagnetSearchPatch.dylib | grep -E "onChange|swift_get|Ts5"
# 应该能看到 8 条：onChange 两种名字 + QOMQ 两种 + Ts5 + 3 个运行时入口
```

---

## 2. 打补丁

```bash
# 2.1 Info.plist 最低系统版本
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 12.0" "$APP/Contents/Info.plist"

# 2.2 Mach-O minos 14.0 -> 12.0（等长字段原地改）
python3 patch/patch_minos.py "$APP/Contents/MacOS/MagnetSearch"

# 2.3 把 5 个符号的导入改成 flat lookup（幂等）
python3 patch/patch_imports.py "$APP/Contents/MacOS/MagnetSearch"

# 2.4 注入补丁库
mkdir -p "$APP/Contents/Frameworks"
cp libMagnetSearchPatch.dylib "$APP/Contents/Frameworks/"
python3 patch/add_load_dylib.py "$APP/Contents/MacOS/MagnetSearch"

# 2.5 重新 ad-hoc 签名
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
```

> `patch_minos.py` / `patch_imports.py` 都是按 **LC 结构**定位，不依赖具体版本；
> 但如果上游换了 SDK，符号清单可能变化——用 `dyld` 的报错迭代即可（见 COMPATIBILITY.md）。

---

## 3. 去掉试用/激活（可选）

```bash
python3 patch/patch_license.py "$APP/Contents/MacOS/MagnetSearch"
```

改三处函数入口（x86_64）：

| 虚拟地址 | 函数 | 写入字节 |
|---|---|---|
| `0x100013380` | `LicenseManager.isActivated.getter` | `B0 01 C3`（`mov al,1; ret`） |
| `0x1000133E0` | `LicenseManager.remainingTrialTime.getter` | `B8 FF FF FF 7F C3` |
| `0x100013C40` | `LicenseManager.isTrialExpired()` | `31 C0 C3`（`xor eax,eax; ret`） |

> 偏移是 **upstream build 1.0** 的。换版本后需要用 `nm -n | grep LicenseManager` 重新定位。

---

## 4. 打服务二进制（可选）

```bash
pip3 install pyinstaller
python3 -m PyInstaller --onefile --name magnetsearch-server \
  --hidden-import torznab_server --hidden-import sources \
  --distpath dist server/run_server.py
```

不打包也行，直接 `python3 server/torznab_server.py`。

---

## 5. 打 pkg

```bash
bash packaging/build_pkg.sh "$APP"
```

要点（踩过的坑）：

1. **`pkgbuild` 会自动生成 `<relocate>`**，导致 Installer 把 app 装到磁盘上已有的同 id 副本那里
   （典型症状：装完 `/Applications` 里没有，日志里出现 `relocated to ...`）。
   构建脚本会在 flatten 前把 `<relocate>` 和 `<upgrade-bundle>` 删掉。
2. `postinstall` 需要给**当前登录用户**装 LaunchAgent（不是 root 的），用
   `stat -f%Su /dev/console` 取用户 + `launchctl asuser $UID` 加载。
3. 版本号要跟着涨，避免和旧收据打架。

---

## 6. 打 dmg

```bash
bash packaging/build_dmg.sh "$APP"
```

目录结构：`MagnetSearch.app`、`应用程序 -> /Applications`、`首次打开.command`、`使用说明.txt`。

---

## 7. 自测清单

```bash
# 从零状态验证
defaults write com.magnetsearch.app trial_start_time -date "2020-01-01 00:00:00 +0000"
open -a /Applications/MagnetSearch.app        # 必须直接进主界面，不能出激活页

curl -s http://127.0.0.1:3333/health          # ok
curl -s "http://127.0.0.1:3333/torznab/api?t=search&q=matrix" | grep -c "<item>"
curl -s "http://127.0.0.1:3333/torznab/api?t=search&q=沙丘2"  | grep -c "<item>"
```
