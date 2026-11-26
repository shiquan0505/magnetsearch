# 低版本兼容修复原理

目标：把一份 `minos 14.0 / SDK 26.2` 的 SwiftUI 二进制，降到能在 macOS 12+ 启动并正常工作。

三个阶段：**骗过启动检查 → 补上缺失符号 → 重新签名**。

---

## 1. 两个启动检查

### 1.1 Info.plist

```xml
<key>LSMinimumSystemVersion</key><string>14.0</string>
```

LaunchServices 见到它就拒绝启动（"该 app 需要 macOS 14 或更高版本"）。改成 `12.0` 即可。

### 1.2 Mach-O 加载命令

```
LC_BUILD_VERSION
  platform 1
  minos 14.0      <- 改成 12.0
  sdk 26.2
```

`minos` 是 uint32，编码为 `(major << 16) | (minor << 8) | patch`。
`14.0` = `0x000E0000` → 改成 `12.0` = `0x000C0000`，**等长字段原地改**，不影响文件布局：

```python
# 结构：cmd(4) cmdsize(4) platform(4) minos(4) sdk(4) ntools(4)
# minos 在命令体 offset+12
```

---

## 2. 缺失符号从哪来

上游用 Xcode 26（Swift 6 / macOS 26 SDK）编译，`minos 14.0`，于是可以放心使用一批
macOS 14+ 才存在的符号。在 macOS 13 上 dyld 会直接报错终止：

```
dyld: Symbol not found: _$s7SwiftUI4ViewPAAE8onChange2of7initial_...
  Referenced from: MagnetSearch
  Expected in: /System/Library/Frameworks/SwiftUI.framework/Versions/A/SwiftUI
```

逐个试出来的完整清单（5 个）：

| 符号 | 含义 | macOS 13 上缺什么 |
|---|---|---|
| `$s7SwiftUI4ViewPAAE8onChange2of7initial_Qrqd___Sbyqd___qd__tctSQRd__lF` | `SwiftUI.View.onChange(of:initial:_:)` | 该 API 是 macOS 14 新增 |
| 同上前缀 + `QOMQ` | 它的 opaque 类型 conformance 描述符 | 同上 |
| `$ss12_ArrayBufferV19_getElementSlowPathyyXlSiFyXl_Ts5` | `_ArrayBuffer<AnyObject>._getElementSlowPath` 的泛型预特化 thunk | Swift 6 标准库才导出；13 上只有 `yyXlSiF` 本体 |
| `swift_getOpaqueTypeConformance2` | Swift 6 运行时入口 | 13 只有无 `2` 的旧版 |
| `swift_getTypeByMangledNameInContext2` | 同上 | 同上 |
| `swift_getTypeByMangledNameInContextInMetadataState2` | 同上 | 同上 |

> 找出清单的方法：`dyld` 每次只报第一个缺失符号，所以是"补一个 → 再跑 → 看下一个"的迭代过程。

---

## 3. 两级命名空间 vs flat lookup

问题：即使你把符号放进一个注入的 dylib，dyld 也不会用——因为主程序的导入是
**两级命名空间（two-level namespace）**：每个外部符号绑定了"来自哪个库的序号"。

```
import#329  lib_ord=8  weak=0  _$s7SwiftUI4ViewPAAE8onChange2of7initial_..._lF
                          ^ 库序号，指向 SwiftUI
```

dyld 只会去 SwiftUI 里找，找不到就报错，压根不看你的 dylib。

解决：把这几条导入的 `lib_ordinal` 改成 **flat lookup（`0xFE` = -2）**，dyld 就会在所有已加载镜像里
按加载顺序查找。SwiftUI 先加载，因此在 macOS 14+ 上仍然会命中系统真符号；13 上则会命中我们的补丁库。

实现见 `patch/patch_imports.py`：

```python
# LC_DYLD_CHAINED_FIXUPS → dyld_chained_fixups_header → imports 表
# 每个 dyld_chained_import: bits[0:8]=lib_ordinal, bits[8]=weak, bits[9:32]=name_offset
d[e:e+4] = struct.pack('<I', (raw & ~0xff) | 0xFE)
```

---

## 4. 补丁库（libMagnetSearchPatch.dylib）

三个文件，全部是"用旧系统已有的东西拼出新的行为"：

### 4.1 `shim.swift` — onChange 的等价实现

```swift
public extension View {
    func onChange<V: Equatable>(of value: V, initial: Bool, _ action: @escaping (V, V) -> Void) -> some View
```

做法：用 macOS 11+ 就有的 `onChange(of:perform:)` 转发（`initial: true` 时额外挂一次 `onAppear`）。
符号名通过 `ld -alias` 直接导出成上游期望的 mangled name：

```bash
-Xlinker -alias -Xlinker <我们模块里的符号> -Xlinker <SwiftUI 的符号名>
```

这样 ABI 由 Swift 编译器保证一致（泛型签名、opaque 返回都对得上），不需要手写汇编。

### 4.2 `slowpath.swift` — 数组慢路径

`_ArrayBuffer<AnyObject>` 的慢路径用于**桥接数组**（例如 `NSRegularExpression.matches()` 返回的
`NSArray`）。macOS 13 没有 `Ts5` 这个 thunk，但 `yyXlSiF` 本体存在；直接转发的坑是
**调用约定**：方法调用的 `self` 不在 rdi。

实测调用点（`otool -tV` 反汇编 `parseTorznabResponse`）：

```
movq %r14, %rdi     ; index
movq %rcx, %rsi     ; self (_ArrayBuffer<AnyObject> 单字)
callq Ts5
```

最终选择更稳的实现：把 buffer 位模式 bitcast 成 `[AnyObject]`，再桥接成 `NSArray` 取元素，
绕开 5.7 标准库里已经不存在的那条路径：

```swift
@_silgen_name("$ss12_ArrayBufferV19_getElementSlowPathyyXlSiFyXl_Ts5")
public func ms_arrayBufferSlowPath(_ index: Int, _ bufferWord: UnsafeRawPointer) -> AnyObject {
    let arr = unsafeBitCast(bufferWord, to: [AnyObject].self)
    return (arr as NSArray).object(at: index) as AnyObject
}
```

### 4.3 `rt_clean.c` — 运行时入口转发

| 新符号 | 转发到（macOS 12/13 已有） |
|---|---|
| `swift_getTypeByMangledNameInContext2` | `swift_getTypeByMangledNameInContext` |
| `swift_getTypeByMangledNameInContextInMetadataState2` | `...InMetadataState` |
| `swift_getOpaqueTypeConformance2` | `swift_getOpaqueTypeConformance` |

注意参数顺序：5.7 的 `swift_getOpaqueTypeConformance` 已经是 `(arguments, descriptor, index)`，
与新的 `...2` 一致——一开始按老印象写成 `(descriptor, arguments, index)` 会直接段错误。

---

## 5. 注入方式

给主程序追加一条加载命令（`patch/add_load_dylib.py`）：

```
LC_LOAD_DYLIB @executable_path/../Frameworks/libMagnetSearchPatch.dylib
```

利用 `__TEXT` 头部 padding 原地写入（本项目实测 header 剩余 5 144 字节，够放一条 88 字节的 LC），
不移动任何 section。最后 `codesign --force --deep --sign -` 重新 ad-hoc 签名。

---

## 6. 为什么 macOS 11 及以下不行

| 依赖 | 最低版本 |
|---|---|
| `LC_DYLD_CHAINED_FIXUPS`（dyld4 链式修复） | macOS 12 |
| `/usr/lib/swift/libswift_Concurrency.dylib` | macOS 12 |
| 本补丁库的编译目标 `-target x86_64-apple-macos12.0` | macOS 12 |

要在 11 上跑，得把链式修复降级成经典 bind 信息、并自带 Swift 并发运行库——工程量远超收益，
且上游 app 大量使用 Swift Concurrency，不建议尝试。

---

## 7. 验证清单

```bash
# 版本与架构
/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" MagnetSearch.app/Contents/Info.plist
otool -l MagnetSearch.app/Contents/MacOS/MagnetSearch | grep -A3 LC_BUILD_VERSION

# 符号是否被改成 flat
python3 patch/patch_imports.py MagnetSearch.app/Contents/MacOS/MagnetSearch   # 幂等，会打印命中的 import#

# 签名
codesign --verify --deep --strict --verbose=2 MagnetSearch.app

# 运行
./MagnetSearch.app/Contents/MacOS/MagnetSearch    # 直接跑，看 dyld 报错
```
