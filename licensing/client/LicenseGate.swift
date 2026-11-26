import AppKit
import Foundation

/// 门禁总控：启动时判断「已买断 / 试用中 / 试用结束」，结束后拉起收银台。
///
/// 判定优先级：
///   1. 本地有服务端签名且未过期的 license token  → 已买断（断网也认）
///   2. 服务端说 licensed                        → 写入 token
///   3. 服务端说 trial                           → 试用中（可离线续用至 trialEnd）
///   4. 服务端说 trial_expired / blocked          → 收银台
///   5. 断网且本地无有效凭证                      → 收银台（离线提示 + 重试）
final class LicenseGate: NSObject {

    static let shared = LicenseGate()

    private var state = LocalState.empty
    private var paywall: PaywallWindow?
    private var orderID: String?
    private var payURL: String?
    private var pollTimer: Timer?
    private var countdownTimer: Timer?
    private var claimTimer: Timer?
    private var claimBusy = false
    private var recheckTimer: Timer?
    private var starting = false
    private var ordering = false
    private var payMode = "platform"        // platform（平台订单）| paywall（付费墙 + 授权码）
    private var paywallURL: String?
    private var paywallQRMode = "image"
    private var paywallQRPayload: String?
    private var paywallNote = "请使用支付宝扫码支付"
    private var menuInstalled = false
    private var licenseMenuItem: NSMenuItem?
    private var lastServerError: String?

    // ---- 给设置页面板用的只读接口 ----
    var isLicensedNow: Bool { validLicense() != nil }
    var remainingSecondsForDisplay: Int? { trialRemainingLocal() }

    func manualShowPaywall() {
        showPaywall(offline: false)
    }

    func updateWindowCountdown() {
        if let remaining = trialRemainingLocal(), remaining > 0 {
            updateMenuCountdown(countdownText(remaining))
        } else if isLicensedNow {
            updateMenuCountdown("已授权（终身）")
        }
    }

    // MARK: - 启动

    func boot() {
        if ProcessInfo.processInfo.environment["MS_LICENSE_DISABLE"] == "1" {
            NSLog("[LIC] MS_LICENSE_DISABLE=1，跳过授权校验")
            return
        }
        NSLog("[LIC] 设备指纹 %@  服务端 %@", DeviceIdentity.deviceID, LicenseAPI.baseURL.absoluteString)
        if Thread.isMainThread {
            installHooks()
        } else {
            DispatchQueue.main.async { self.installHooks() }
        }
    }

    private func installHooks() {
        NotificationCenter.default.addObserver(forName: NSApplication.didFinishLaunchingNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            self?.delayedStart()
        }
        if let app = NSApp, app.isRunning {
            delayedStart()
        } else {
            // 极端情况：didFinishLaunching 已经过去了
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                guard let self = self, !self.starting else { return }
                self.delayedStart()
            }
        }
    }

    private func delayedStart() {
        guard !starting else { return }
        starting = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in
            self?.start()
        }
    }

    private func start() {
        state = LocalState.load()
        scheduleMenuInstall()
        SettingsLicenseInjector.shared.start()
        if let p = validLicense() {
            NSLog("[LIC] 本地授权有效 plan=%@ exp=%@", p.plan ?? "-", p.expiresAt.map { "\($0)" } ?? "永久")
            updateWindowCountdown()
            scheduleRecheck()
            // 后台静默复核一次：断网照样可用，联网时能第一时间发现退款/吊销
            refreshFromServer(initial: false)
            return
        }
        refreshFromServer(initial: true)
    }

    // MARK: - 本地凭证

    private func validLicense() -> LicenseToken.Payload? {
        guard let tok = state.licenseToken else { return nil }
        guard let p = try? LicenseToken.verify(tok, expectingDevice: DeviceIdentity.deviceID,
                                               now: LocalState.effectiveDate(state)) else { return nil }
        return p.typ == "license" ? p : nil
    }

    private func trialRemainingLocal() -> Int? {
        var end = state.trialEnd
        if let tok = state.trialToken,
           let p = try? LicenseToken.verify(tok, expectingDevice: DeviceIdentity.deviceID,
                                            now: LocalState.effectiveDate(state)),
           let te = p.trialEnd {
            end = te
            state.trialEnd = te
        }
        guard let e = end else { return nil }
        let now = LocalState.effectiveNow(state)
        return e - now
    }

    // MARK: - 联网校验

    func refreshFromServer(initial: Bool = false, completion: ((String) -> Void)? = nil) {
        LicenseAPI.session { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                switch result {
                case .success(let data):
                    self.lastServerError = nil
                    self.handleSession(data, initial: initial)
                    completion?(data["state"] as? String ?? "")
                case .failure(let err):
                    let msg = (err as? LicenseAPI.APIError)?.description ?? err.localizedDescription
                    self.lastServerError = msg
                    NSLog("[LIC] 联网校验失败：%@", msg)
                    self.handleOffline(msg)
                    completion?("offline")
                }
            }
        }
    }

    private func handleSession(_ data: [String: Any], initial: Bool) {
        let st = (data["state"] as? String) ?? ""
        payMode = (data["pay_mode"] as? String) ?? "platform"
        if let pw = data["paywall"] as? [String: Any] {
            paywallURL = (pw["url"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            paywallQRMode = (pw["qr_mode"] as? String) ?? "image"
            paywallQRPayload = (pw["qr_payload"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            if let n = pw["note"] as? String, !n.isEmpty { paywallNote = n }
        }
        if let tok = data["trial_token"] as? String,
           let p = try? LicenseToken.verify(tok, expectingDevice: DeviceIdentity.deviceID) {
            state.trialToken = tok
            state.trialEnd = p.trialEnd
        }
        if let serverNow = data["server_time"] as? Int {
            state.lastCheck = serverNow
            state.serverTimeOffset = serverNow - LocalState.now
            state.clockFloor = max(state.clockFloor, serverNow)
        } else {
            state.lastCheck = LocalState.now
            state.clockFloor = max(state.clockFloor, LocalState.now)
        }
        LocalState.save(state)

        if st != "licensed" && st != "trial" && state.licenseToken != nil {
            // 服务端明确表示不再是有授权状态（退款/吊销）→ 清掉本地凭证
            state.licenseToken = nil
            LocalState.save(state)
            NSLog("[LIC] 服务端状态=%@，清除本地授权凭证", st)
        }

        switch st {
        case "licensed":
            if let tok = data["license_token"] as? String,
               let p = try? LicenseToken.verify(tok, expectingDevice: DeviceIdentity.deviceID) {
                state.licenseToken = tok
                LocalState.save(state)
                NSLog("[LIC] 已买断 plan=%@ exp=%@", p.plan ?? "-",
                      p.expiresAt.map { "\($0)" } ?? "永久")
            }
            dismissPaywall()
            stopPolling()
            updateMenuCountdown("已授权（终身）")
            scheduleRecheck()

        case "trial":
            scheduleRecheck()
            let remaining = (data["trial_remaining"] as? Int) ?? trialRemainingLocal() ?? 0
            NSLog("[LIC] 试用中，剩余 %d 秒（%.1f 天）", remaining, Double(remaining) / 86400.0)
            updateMenuCountdown(countdownText(remaining))
            scheduleCountdownRefresh()
            if initial && !state.firstRunAcknowledged {
                state.firstRunAcknowledged = true
                LocalState.save(state)
                showTrialWelcome(remaining)
            }

        case "trial_expired":
            updateMenuCountdown("试用已结束 · 购买授权…")
            scheduleRecheck()
            if (data["pending_order_id"] as? String) != nil {
                orderID = data["pending_order_id"] as? String
            }
            showPaywall(offline: false)

        case "blocked":
            showPaywall(blocked: true)

        default:
            NSLog("[LIC] 未知状态：%@", st)
            handleOffline("服务端状态异常")
        }
    }

    private func handleOffline(_ msg: String) {
        if validLicense() != nil {
            NSLog("[LIC] 离线但本地授权有效，放行")
            scheduleRecheck()
            return
        }
        if let remaining = trialRemainingLocal(), remaining > 0 {
            NSLog("[LIC] 离线试用中，剩余 %d 秒", remaining)
            scheduleRecheck()
            return
        }
        showPaywall(offline: true, message: msg)
    }

    // MARK: - 定时复查

    private func scheduleRecheck() {
        recheckTimer?.invalidate()
        let hours = max(1, LicenseConfig.recheckHours)
        recheckTimer = Timer.scheduledTimer(withTimeInterval: Double(hours) * 3600, repeats: true) { [weak self] _ in
            self?.refreshFromServer()
        }
        if let t = recheckTimer { RunLoop.main.add(t, forMode: .common) }
    }

    private func scheduleCountdownRefresh() {
        countdownTimer?.invalidate()
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1800, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            if let remaining = self.trialRemainingLocal() {
                self.updateMenuCountdown(self.countdownText(remaining))
            }
            if let p = self.validLicense() {
                _ = p
            }
        }
        if let t = countdownTimer { RunLoop.main.add(t, forMode: .common) }
    }

    private func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    // MARK: - 收银台

    private func ensurePaywall() -> PaywallWindow {
        if let p = paywall { return p }
        let w = PaywallWindow()
        w.onRefresh = { [weak self] in self?.onPaywallRefresh() }
        w.onOpenPayPage = { [weak self] in self?.openPayPage() }
        w.onQuit = { NSApp.terminate(nil) }
        w.onRedeem = { [weak self] code in self?.redeem(code) }
        paywall = w
        return w
    }

    /// 只负责"把该显示的显示出来"，绝不串接网络回调 —— 否则 session → 收银台 → 查单 → session 会打转。
    private func showPaywall(offline: Bool = false, blocked: Bool = false, message: String? = nil) {
        NSLog("[LIC] 显示收银台 offline=%d blocked=%d msg=%@", offline ? 1 : 0, blocked ? 1 : 0,
              message ?? "-")
        let w = ensurePaywall()

        if blocked {
            var c = PaywallContent()
            c.headline = "该设备已被停用"
            c.subline = "如需恢复使用，请联系客服"
            c.status = "授权已停用"
            c.showBuyButtons = false
            w.show(content: c)
            return
        }

        if offline {
            var c = PaywallContent()
            c.headline = paywallHeadline()
            c.subline = "需要连接授权服务器完成解锁"
            c.status = message ?? "网络不可用"
            c.footer = "确认网络后点「重新连接」；已付款但未解锁也可以重试"
            c.showOpenPage = false
            c.refreshTitle = " 重新连接 "
            w.show(content: c)
            return
        }

        if payMode == "paywall" {
            renderPaywallMode(visible: w.isVisible)
            return
        }

        // 已经显示着：保持内容，最多修正一下状态文案，不重复下单/查单
        if w.isVisible {
            if orderID == nil || payURL == nil {
                w.updateStatus("正在生成支付二维码…")
                if !ordering { requestOrder() }
            }
            return
        }

        var c = PaywallContent()
        c.status = orderID == nil ? "正在生成支付二维码…" : "支付宝 / 微信 扫码支付"
        w.show(content: c)
        // 只有 orderID 没有二维码内容（比如进程重启后从服务端带回 pending_order_id）时，
        // 也走一次下单接口：服务端会复用未过期的待支付订单并带回 qr_content。
        if orderID == nil || payURL == nil {
            requestOrder()
        } else if let oid = orderID, let url = payURL {
            renderExistingOrder(orderID: oid, qr: url)
            startPolling()
        }
    }

    /// 付费墙模式：
    ///   优先把付费页里面那个收款码解出来（qr_mode=image），直接显示，标注「请使用支付宝扫码支付」；
    ///   解不出来就退回显示付费页地址（qr_mode=page 或网络异常时）。
    /// 收银台标题按**真实状态**来：还在试用就别写「试用已结束」
    private func paywallHeadline() -> String {
        if validLicense() != nil { return "已授权 · 终身可用" }
        if let r = trialRemainingLocal(), r > 0 {
            let days = r / 86400
            let hours = (r % 86400) / 3600
            if days > 0 { return "试用中 · 剩余 \(days) 天 \(hours) 小时" }
            return "试用中 · 剩余 \(max(1, hours)) 小时"
        }
        return "MagnetSearch 试用已结束"
    }

    private func renderPaywallMode(visible: Bool) {
        let amount = String(format: "%.2f", Double(LicenseConfig.priceCents) / 100.0)
        func content(payload: String?, isDirect: Bool) -> PaywallContent {
            var c = PaywallContent()
            c.headline = paywallHeadline()
            c.subline = "\(paywallNote) ¥\(amount)"
            if let p = payload, let img = PaywallWindow.qrImage(p) {
                c.qrImage = img
                c.status = isDirect ? "支付宝扫一扫 → 付款成功会自动跳转授权页"
                                    : "扫码后按页面提示付款，成功即可领授权码"
            } else {
                c.status = "点击「打开支付页面」完成付款"
            }
            c.footer = "授权码一次性有效，只解锁这一台设备（\(DeviceIdentity.deviceID.prefix(12))…）"
            c.refreshTitle = " 我已付款，刷新 "
            c.showCodeEntry = true
            return c
        }

        // 先把已知信息画出来（避免弹窗空着）
        if paywallQRMode == "image", let cached = paywallQRPayload {
            paywall?.show(content: content(payload: cached, isDirect: true))
        } else if paywallQRMode == "page", let pageURL = paywallURL {
            paywall?.show(content: content(payload: pageURL, isDirect: false))
        } else {
            paywall?.show(content: content(payload: nil, isDirect: false))
            paywall?.updateStatus("正在获取收款码…")
        }

        startClaimPolling()

        guard paywallQRMode == "image", let pageURL = paywallURL, paywallQRPayload == nil else { return }
        PaywallQR.resolve(pageURL: pageURL, fallback: pageURL) { [weak self] result in
            guard let self = self else { return }
            if result.isDirect { self.paywallQRPayload = result.payload }
            self.paywall?.show(content: content(payload: result.payload, isDirect: result.isDirect))
            if !visible { NSLog("[LIC] 付费墙二维码：%@（直接收款码=%@）", result.payload, result.isDirect ? "是" : "否") }
        }
    }

    /// 付费墙模式：付款后平台会把用户跳到 /claim/<secret> 发一张码（绑出口 IP），
    /// 这里每 4 秒问一次「我这个 IP 有没有未用的码」，有就直接兑换解锁 —— 用户不用输码。
    private func startClaimPolling() {
        claimTimer?.invalidate()
        let t = Timer.scheduledTimer(withTimeInterval: 4.0, repeats: true) { [weak self] _ in
            self?.pollClaimOnce(silent: true)
        }
        RunLoop.main.add(t, forMode: .common)
        claimTimer = t
        pollClaimOnce(silent: false)
    }

    private func stopClaimPolling() {
        claimTimer?.invalidate()
        claimTimer = nil
    }

    private func pollClaimOnce(silent: Bool) {
        guard payMode == "paywall", !claimBusy, paywall?.isVisible == true else { return }
        claimBusy = true
        LicenseAPI.claimAuto { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.claimBusy = false
                switch result {
                case .success(let d):
                    if let code = d["code"] as? String, !code.isEmpty {
                        NSLog("[LIC] 自动取到授权码 %@，开始兑换", code)
                        self.stopClaimPolling()
                        self.redeem(code)
                    } else if !silent {
                        self.paywall?.updateStatus("还没收到付款记录，付款后请稍等几秒…")
                    }
                case .failure(let err):
                    if !silent {
                        let msg = (err as? LicenseAPI.APIError)?.description ?? err.localizedDescription
                        self.paywall?.updateStatus(msg)
                    }
                }
            }
        }
    }

    private func redeem(_ rawCode: String) {
        let code = rawCode.trimmingCharacters(in: .whitespacesAndNewlines)
        paywall?.updateStatus("正在验证授权码…")
        NSLog("[LIC] 兑换授权码 %@", code)
        LicenseAPI.redeem(code: code) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                switch result {
                case .success(let d):
                    if let tok = d["license_token"] as? String,
                       let p = try? LicenseToken.verify(tok, expectingDevice: DeviceIdentity.deviceID) {
                        self.state.licenseToken = tok
                        self.state.trialEnd = p.trialEnd
                        LocalState.save(self.state)
                        NSLog("[LIC] 授权码兑换成功，已写入永久授权")
                        self.paywall?.updateStatus("已开通，正在解锁…")
                        self.dismissPaywall()
                        self.updateWindowCountdown()   // 清掉 Dock「已到期」徽标与标题后缀
                        self.scheduleRecheck()
                    } else {
                        self.paywall?.updateStatus("兑换成功但凭证异常，请联系客服")
                    }
                case .failure(let err):
                    let msg = (err as? LicenseAPI.APIError)?.description ?? err.localizedDescription
                    NSLog("[LIC] 兑换失败：%@", msg)
                    self.paywall?.updateStatus(msg)
                }
            }
        }
    }

    private func requestOrder() {
        guard !ordering else { return }
        ordering = true
        paywall?.updateStatus("正在生成支付二维码…")
        NSLog("[LIC] 请求下单 device=%@", DeviceIdentity.deviceID)
        LicenseAPI.createOrder { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.ordering = false
                switch result {
                case .success(let d):
                    NSLog("[LIC] 下单返回 order=%@", (d["order_id"] as? String) ?? "-")
                    if d["already_licensed"] as? Bool == true {
                        self.refreshFromServer()
                        return
                    }
                    self.orderID = d["order_id"] as? String
                    self.payURL = d["pay_url"] as? String ?? d["qr_content"] as? String
                    self.state.lastPaywallOrderID = self.orderID
                    LocalState.save(self.state)
                    self.renderOrder(d)
                    self.startPolling()
                case .failure(let err):
                    let msg = (err as? LicenseAPI.APIError)?.description ?? err.localizedDescription
                    NSLog("[LIC] 下单失败：%@", msg)
                    self.paywall?.updateStatus("下单失败：\(msg)")
                }
            }
        }
    }

    private func renderOrder(_ d: [String: Any]) {
        let amount = (d["amount_cents"] as? Int).map { String(format: "%.2f", Double($0) / 100.0) }
            ?? String(format: "%.2f", Double(LicenseConfig.priceCents) / 100.0)
        let qr = (d["qr_content"] as? String) ?? ""
        var c = PaywallContent()
        c.headline = paywallHeadline()
        c.subline = "扫码支付 ¥\(amount)，本机立即永久解锁"
        c.qrImage = PaywallWindow.qrImage(qr)
        c.status = c.qrImage == nil ? "无法生成二维码，请点「打开支付页面」" : "支付宝 / 微信 扫码支付"
        let oid = (d["order_id"] as? String) ?? "-"
        c.footer = "订单 \(oid) · 设备 \(DeviceIdentity.deviceID.prefix(12))…"
        paywall?.show(content: c)
    }

    private func renderExistingOrder(orderID oid: String, qr: String) {
        renderOrder(["order_id": oid, "qr_content": qr,
                     "amount_cents": LicenseConfig.priceCents])
    }

    private func startPolling() {
        stopPolling()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.refreshOrder()
        }
        if let t = pollTimer { RunLoop.main.add(t, forMode: .common) }
    }

    /// 查一次订单状态；未支付就等下一轮轮询，**不**回头触发 session（那是死循环的源头）。
    private func refreshOrder() {
        guard let oid = orderID else { return }
        LicenseAPI.orderStatus(oid) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self, case .success(let d) = result else { return }
                if d["paid"] as? Bool == true {
                    self.paywall?.updateStatus("支付成功，正在解锁…")
                    self.stopPolling()
                    self.orderID = nil
                    self.refreshFromServer()
                }
            }
        }
    }

    private func onPaywallRefresh() {
        if payMode == "paywall" {
            paywall?.updateStatus("正在确认付款…")
            pollClaimOnce(silent: false)
            return
        }
        if orderID != nil {
            paywall?.updateStatus("正在确认支付结果…")
            refreshOrder()
        } else {
            paywall?.updateStatus("正在连接授权服务器…")
            refreshFromServer()
        }
    }

    private func openPayPage() {
        if payMode == "paywall", let p = paywallURL, let u = URL(string: p) {
            NSWorkspace.shared.open(u)
            return
        }
        let url: URL?
        if let p = payURL, let u = URL(string: p), u.scheme?.hasPrefix("http") == true {
            url = u
        } else if let oid = orderID {
            url = URL(string: "\(LicenseConfig.serverBase)/pay/\(oid)")
        } else {
            url = nil
        }
        if let u = url { NSWorkspace.shared.open(u) }
    }

    private func dismissPaywall() {
        stopClaimPolling()
        // 只收起窗口，保留实例：下次再需要时同一份 UI 直接复用（避免窗口重建带来的时序问题）
        paywall?.close()
        orderID = nil
    }

    // MARK: - 首次试用提示

    private func showTrialWelcome(_ remainingSeconds: Int) {
        let days = max(0, Int(ceil(Double(remainingSeconds) / 86400.0)))
        let price = String(format: "%.0f", Double(LicenseConfig.priceCents) / 100.0)
        let alert = NSAlert()
        alert.messageText = "已开始 \(LicenseConfig.trialDays) 天免费试用"
        alert.informativeText = "当前还剩约 \(days) 天。试用结束后需要购买授权（¥\(price)，一次性买断、终身可用、无需卡密）。"
        alert.addButton(withTitle: "开始使用")
        alert.addButton(withTitle: "了解购买")

        // 非阻塞：挂到主窗口的 sheet 上；找不到窗口才退回 runModal。
        // 之前用 runModal 时，如果 app 不是前台，模态会立刻返回，被误判成"用户点了了解购买"。
        let shownAt = Date()
        let handler: (NSApplication.ModalResponse) -> Void = { [weak self] resp in
            let elapsed = Date().timeIntervalSince(shownAt)
            NSLog("[LIC] 试用提示选择：%ld（%.2fs）", resp.rawValue, elapsed)
            // 不到 0.4 秒就返回的，是系统把 sheet 收掉了，不当成用户点「了解购买」
            guard elapsed > 0.4 else { return }
            if resp == .alertSecondButtonReturn {
                self?.showPaywall(offline: false)
            }
        }
        if let host = NSApp.mainWindow ?? NSApp.windows.first(where: { $0.isVisible && $0.canBecomeKey }) {
            alert.beginSheetModal(for: host, completionHandler: handler)
        } else {
            NSApp.activate(ignoringOtherApps: true)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                handler(alert.runModal())
            }
        }
    }

    // MARK: - 菜单项（尽力而为）

    /// SwiftUI 的菜单栏不是启动瞬间就绪的，这里退避重试直到挂上为止
    private func scheduleMenuInstall(_ attempt: Int = 0) {
        if licenseMenuItem != nil { return }
        guard attempt < 6 else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            guard let self = self else { return }
            self.installMenu()
            if self.licenseMenuItem == nil {
                self.scheduleMenuInstall(attempt + 1)
            } else if let remaining = self.trialRemainingLocal(), remaining > 0 {
                self.updateMenuCountdown(self.countdownText(remaining))
            } else if self.validLicense() != nil {
                self.updateMenuCountdown("已授权（终身）")
            }
        }
    }

    private func installMenu() {
        guard !menuInstalled, let menu = NSApp.mainMenu else { return }
        menuInstalled = true
        let item = NSMenuItem(title: "授权与购买…", action: #selector(showLicenseInfo), keyEquivalent: "")
        item.target = self
        licenseMenuItem = item
        if let appItem = menu.items.first, let sub = appItem.submenu {
            sub.insertItem(item, at: min(2, sub.numberOfItems))
        } else {
            menu.addItem(item)
        }
    }

    /// 试用倒计时常驻显示：窗口标题后缀 + Dock 徽标（菜单项能挂上也一并更新）
    private func updateMenuCountdown(_ text: String) {
        licenseMenuItem?.title = text
        showCountdownBadge(text)
    }

    private func showCountdownBadge(_ text: String) {
        let short: String?
        if text.hasPrefix("试用剩余") {
            // Dock 徽标只放最关键的：天数（不足一天放小时）
            if let days = Int(text.split(separator: " ").dropFirst().first ?? "") , days > 0 {
                short = "\(days)天"
            } else {
                let parts = text.split(separator: " ")
                short = parts.count > 3 ? "\(parts[3])h" : "试用中"
            }
        } else if text.hasPrefix("试用已结束") {
            short = "已到期"
        } else {
            short = nil
        }
        NSApp.dockTile.badgeLabel = short
        for w in NSApp.windows where w.isVisible && !(w is NSPanel) && w.canBecomeKey {
            if let short = short {
                if !w.title.contains("试用") { w.title = w.title + " · 试用 " + short }
            } else if let r = w.title.range(of: " · 试用 ") {
                w.title.removeSubrange(r.lowerBound..<w.title.endIndex)
            }
        }
    }

    private func countdownText(_ seconds: Int) -> String {
        if seconds <= 0 { return "试用已结束 · 购买授权…" }
        let days = seconds / 86400
        let hours = (seconds % 86400) / 3600
        if days >= 1 { return "试用剩余 \(days) 天 \(hours) 小时 · 购买授权…" }
        return "试用剩余 \(max(1, hours)) 小时 · 购买授权…"
    }

    @objc private func showLicenseInfo() {
        if let p = validLicense() {
            let exp = p.expiresAt.map { Date(timeIntervalSince1970: TimeInterval($0)).description } ?? "永久"
            let a = NSAlert()
            a.messageText = "已授权"
            a.informativeText = "类型：\(p.plan ?? "lifetime")\n有效期：\(exp)\n设备：\(DeviceIdentity.deviceID)"
            a.addButton(withTitle: "好")
            a.runModal()
            return
        }
        if let remaining = trialRemainingLocal(), remaining > 0 {
            let a = NSAlert()
            a.messageText = "试用中"
            a.informativeText = "\(countdownText(remaining))\n设备：\(DeviceIdentity.deviceID)"
            a.addButton(withTitle: "继续试用")
            a.addButton(withTitle: "立即购买")
            if a.runModal() == .alertSecondButtonReturn { showPaywall(offline: false) }
        } else {
            showPaywall(offline: false)
        }
    }
}
