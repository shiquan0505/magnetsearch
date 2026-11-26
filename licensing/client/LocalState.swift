import Foundation

/// 本地状态：只做缓存，**权威数据永远在服务端**。
/// 文件里存的 token 都是服务端签名的，改一个字节就验不过 → 自动回落到联网校验。
struct LocalState: Codable {
    var licenseToken: String?
    var trialToken: String?
    var trialEnd: Int?
    var installedAt: Int
    var lastCheck: Int
    /// 见过的最大时间戳：防止把系统时钟往回调来续试用
    var clockFloor: Int
    var firstRunAcknowledged: Bool
    var lastPaywallOrderID: String?
    /// 上次联网时「服务端时间 - 本地时间」，用来抵消把系统时钟往回调的行为
    var serverTimeOffset: Int?
    /// 用户点"关闭"后，多久之内不再自动弹收银台
    var paywallSnoozedUntil: Int?

    static let empty = LocalState(licenseToken: nil, trialToken: nil, trialEnd: nil,
                                  installedAt: Int(Date().timeIntervalSince1970),
                                  lastCheck: 0,
                                  clockFloor: Int(Date().timeIntervalSince1970),
                                  firstRunAcknowledged: false,
                                  lastPaywallOrderID: nil,
                                  serverTimeOffset: nil,
                                  paywallSnoozedUntil: nil)

    static var now: Int { Int(Date().timeIntervalSince1970) }

    // MARK: - 存储位置

    static func directory() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory() + "/Library/Application Support")
        let dir = base.appendingPathComponent("MagnetSearchLicense", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static func fileURL() -> URL {
        directory().appendingPathComponent("state.json")
    }

    // MARK: - 读写

    static func load() -> LocalState {
        var state: LocalState?
        if let data = try? Data(contentsOf: fileURL()),
           let s = try? JSONDecoder().decode(LocalState.self, from: data) {
            state = s
        }
        // 第二份：UserDefaults（用户删掉 json 也还在）
        if state == nil, let data = UserDefaults.standard.data(forKey: defaultsKey),
           let s = try? JSONDecoder().decode(LocalState.self, from: data) {
            state = s
        }
        var s = state ?? .empty
        // 时钟回拨保护：只抬高水位，不因系统时间变小而降低
        let realNow = now
        if realNow > s.clockFloor { s.clockFloor = realNow }
        return s
    }

    static let defaultsKey = "com.magnetsearch.license.state"

    static func save(_ state: LocalState) {
        if let data = try? JSONEncoder().encode(state) {
            try? data.write(to: fileURL(), options: [.atomic])
            UserDefaults.standard.set(data, forKey: defaultsKey)
            UserDefaults.standard.synchronize()
        }
    }

    /// 防回拨的“当前时间”：
    ///   * 叠加最近一次联网得到的服务端时间偏移
    ///   * 与历史最高水位（clockFloor）取大
    static func effectiveNow(_ s: LocalState) -> Int {
        let skewed = now + (s.serverTimeOffset ?? 0)
        return max(skewed, s.clockFloor)
    }

    /// 给 Date 形式的接口用
    static func effectiveDate(_ s: LocalState) -> Date {
        Date(timeIntervalSince1970: TimeInterval(effectiveNow(s)))
    }
}
