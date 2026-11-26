import Foundation

/// macOS 13 缺 `_ArrayBuffer<AnyObject>._getElementSlowPath` 的预特化 thunk。
/// 调用点 ABI(x86_64)：rdi = index, rsi = buffer 单字（_ArrayBuffer<AnyObject> 的位模式）。
/// 这里把它桥接成 NSArray 取元素，避开 5.7 标准库里已不存在的那条路径。
@_silgen_name("$ss12_ArrayBufferV19_getElementSlowPathyyXlSiFyXl_Ts5")
public func ms_arrayBufferSlowPath(_ index: Int, _ bufferWord: UnsafeRawPointer) -> AnyObject {
    let arr = unsafeBitCast(bufferWord, to: [AnyObject].self)
    let ns = arr as NSArray
    return ns.object(at: index) as AnyObject
}
