import Foundation
import CryptoKit
import IOKit

/// 设备指纹：IOPlatformUUID（主板 UUID，重装系统才会变）→ SHA256 → 前 32 位十六进制。
/// 服务端只存哈希，拿不到用户原始硬件标识；同一台机器的指纹稳定 → 重装 app 也重置不了试用。
enum DeviceIdentity {

    private static var _cached: String?

    static var deviceID: String {
        if let c = _cached { return c }
        let raw = platformUUID() ?? serialFallback() ?? installUUID()
        let digest = SHA256.hash(data: Data(("ms-license|" + raw).utf8))
        let hex = digest.compactMap { String(format: "%02x", $0) }.joined()
        let id = "dev-" + String(hex.prefix(32))
        _cached = id
        return id
    }

    static var platformDescription: String {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        #if arch(x86_64)
        let archName = "x86_64"
        #elseif arch(arm64)
        let archName = "arm64"
        #else
        let archName = "unknown"
        #endif
        return "macOS \(v.majorVersion).\(v.minorVersion).\(v.patchVersion) \(archName)"
    }

    static var appVersion: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "0"
        let build = info?["CFBundleVersion"] as? String ?? "0"
        return "\(short)(\(build))"
    }

    // MARK: - 底层取值

    private static func ioString(_ key: String) -> String? {
        let service = IOServiceGetMatchingService(0, IOServiceMatching("IOPlatformExpertDevice"))
        guard service != 0 else { return nil }
        defer { IOObjectRelease(service) }
        guard let cf = IORegistryEntryCreateCFProperty(service, key as CFString, kCFAllocatorDefault, 0)?
            .takeRetainedValue() as? String else { return nil }
        let s = cf.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : s
    }

    private static func platformUUID() -> String? {
        ioString("IOPlatformUUID")
    }

    private static func serialFallback() -> String? {
        ioString("IOPlatformSerialNumber")
    }

    /// 极端兜底（既没有 IOKit 权限又没有 UUID）：用首次安装时生成的随机值，存两份。
    private static func installUUID() -> String {
        let key = "com.magnetsearch.license.installUUID"
        if let s = UserDefaults.standard.string(forKey: key), !s.isEmpty { return s }
        let s = UUID().uuidString
        UserDefaults.standard.set(s, forKey: key)
        return s
    }
}
