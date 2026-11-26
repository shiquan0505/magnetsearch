import Foundation

/// 注入入口：由 libMagnetSearchPatch.dylib（或本库自己的 constructor）调用。
/// 全程只在主线程碰 UI，任何异常都不应该拖垮 app。
@_cdecl("ms_license_boot")
public func ms_license_boot() {
    autoreleasepool {
        LicenseGate.shared.boot()
    }
}

/// 给外部（脚本/调试）用的状态查询
@_cdecl("ms_license_state")
public func ms_license_state() -> UnsafeMutablePointer<CChar>? {
    let env = ProcessInfo.processInfo.environment
    let text = "device=\(DeviceIdentity.deviceID) server=\(LicenseAPI.baseURL.absoluteString)"
        + (env["MS_LICENSE_DISABLE"] == "1" ? " disabled=1" : "")
    return strdup(text)
}
