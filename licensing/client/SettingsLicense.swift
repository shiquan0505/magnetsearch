import AppKit

/// 往 app 自带的「设置」sheet 里注入一块「授权」面板。
///
/// app 是 SwiftUI 二进制、没有源码，所以从 AppKit 层做：
///   1. 监听所有 sheet 窗口，按尺寸/内容特征认出「设置」那个（320~720 宽、220~760 高）
///   2. 把窗口向下加高 178pt，在原内容下方留出空白区
///   3. 用同色遮罩盖掉底部两行无效提示（局域网 IP / 其他设备访问）
///   4. 在空白区画自己的授权面板：状态 + 剩余时间 + 设备号 + [购买/激活] [刷新]
/// SwiftUI 的 contentView 是"翻转"坐标系（y=0 在顶部），自定义面板也用翻转坐标，避免算错
final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

final class SettingsLicenseInjector: NSObject {

    static let shared = SettingsLicenseInjector()

    private var injected = Set<ObjectIdentifier>()
    private var timer: Timer?
    private var panelStatus: NSTextField?
    private var panelFont: NSTextField?

    private let panelHeight: CGFloat = 178

    func start() {
        guard timer == nil else { return }
        let t = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.scan()
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    // MARK: - 找「设置」sheet


    private func scan() {
        for w in NSApp.windows {
            let key = ObjectIdentifier(w)
            guard !injected.contains(key) else { continue }
            guard w.isVisible, w.sheetParent != nil, !(w is NSPanel) else { continue }
            if PaywallWindow.isPaywallWindow(w) { continue }
            let size = w.frame.size
            guard size.width >= 320, size.width <= 720,
                  size.height >= 210, size.height <= 780 else { continue }
            guard looksLikeSettings(w) else { continue }
            inject(into: w)
        }
    }

    /// 设置 sheet 的特征：SwiftUI 把控件画在自己的图层里，view 层级里抓不到 NSTextField，
    /// 所以用「尺寸 + 是 sheet + 不是我们的收银台」来认：app 的设置就是 450x350。
    private func looksLikeSettings(_ w: NSWindow) -> Bool {
        if let content = w.contentView {
            let fields = collectFieldValues(content)
            if fields.contains(where: { $0.lowercased().hasPrefix("http") }) { return true }
            let texts = collectTexts(content).joined(separator: "|")
            if texts.contains("服务器地址") || texts.contains("API Key") { return true }
        }
        let s = w.frame.size
        return s.width >= 380 && s.width <= 520 && s.height >= 260 && s.height <= 430
    }

    private func countTextFields(_ view: NSView?) -> Int {
        guard let view = view else { return 0 }
        return collectFieldValues(view).count
    }

    /// SwiftUI 的 TextField 背后就是 NSTextField，但层级很深，递归要够深
    private func collectFieldValues(_ view: NSView, depth: Int = 0) -> [String] {
        if depth > 14 { return [] }
        var out: [String] = []
        if let tf = view as? NSTextField, tf.isEditable {
            out.append(tf.stringValue)
        }
        for sub in view.subviews { out.append(contentsOf: collectFieldValues(sub, depth: depth + 1)) }
        return out
    }

    private func collectTexts(_ view: NSView, depth: Int = 0) -> [String] {
        if depth > 14 { return [] }
        var out: [String] = []
        if let tf = view as? NSTextField, !tf.isEditable, !tf.stringValue.isEmpty {
            out.append(tf.stringValue)
        }
        for sub in view.subviews {
            out.append(contentsOf: collectTexts(sub, depth: depth + 1))
        }
        return out
    }

    // MARK: - 注入

    private func inject(into w: NSWindow) {
        guard let content = w.contentView else { return }
        injected.insert(ObjectIdentifier(w))
        // 挂到 theme frame 上：SwiftUI 的 hostingView 会吞掉鼠标事件，
        // 挂在它里面按钮点不动；挂在外面既能盖住无效提示，也能正常响应点击。
        let host = content.superview ?? content
        let W = host.bounds.width
        let H = host.bounds.height
        NSLog("[LIC] 命中设置页 %.0fx%.0f（host %.0fx%.0f flipped=%d），替换底部提示为授权栏",
              content.bounds.width, content.bounds.height, W, H, host.isFlipped ? 1 : 0)

        let rowH: CGFloat = 52
        let rowY = host.isFlipped ? H - rowH : 0
        let row = FlippedView(frame: NSRect(x: 0, y: rowY, width: W, height: rowH))
        row.wantsLayer = true
        row.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        let topLine = NSView(frame: NSRect(x: 16, y: 0, width: W - 32, height: 1))
        topLine.wantsLayer = true
        topLine.layer?.backgroundColor = NSColor.separatorColor.cgColor
        row.addSubview(topLine)

        let caption = makeLabel("授权", size: 11, weight: .regular, color: NSColor.secondaryLabelColor)
        caption.frame = NSRect(x: 20, y: 12, width: 40, height: 15)
        row.addSubview(caption)

        let status = makeLabel("", size: 11.5, weight: .medium, color: NSColor.labelColor)
        status.frame = NSRect(x: 20, y: 28, width: W - 40 - 90, height: 16)
        row.addSubview(status)
        panelStatus = status

        let link = NSButton(title: "了解购买 ›", target: self, action: #selector(doBuy))
        link.bezelStyle = .inline
        link.isBordered = false
        link.contentTintColor = NSColor.controlAccentColor
        link.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        link.frame = NSRect(x: W - 20 - 88, y: 18, width: 88, height: 22)
        row.addSubview(link)

        host.addSubview(row)
        refreshPanelText()
    }

    private func makeLabel(_ text: String, size: CGFloat, weight: NSFont.Weight,
                           color: NSColor) -> NSTextField {
        let l = NSTextField(labelWithString: text)
        l.font = NSFont.systemFont(ofSize: size, weight: weight)
        l.textColor = color
        l.lineBreakMode = .byTruncatingTail
        return l
    }

    // MARK: - 刷新文案

    func refreshPanelText() {
        guard let status = panelStatus else { return }
        if LicenseGate.shared.isLicensedNow {
            status.stringValue = "已永久授权 · 终身可用"
            status.textColor = NSColor.systemGreen
        } else if let remaining = LicenseGate.shared.remainingSecondsForDisplay, remaining > 0 {
            let days = remaining / 86400
            let hours = (remaining % 86400) / 3600
            status.stringValue = "试用中 · 剩余 \(days) 天 \(hours) 小时"
            status.textColor = NSColor.secondaryLabelColor
        } else {
            status.stringValue = "试用已结束 · 需要购买授权"
            status.textColor = NSColor.systemOrange
        }
    }

    // MARK: - 按钮

    @objc private func doBuy() {
        NSLog("[LIC] 设置页点击「了解购买」")
        closeSettingsSheet()
        LicenseGate.shared.manualShowPaywall()
    }

    @objc private func doRefresh() {
        panelStatus?.stringValue = "正在刷新…"
        LicenseGate.shared.refreshFromServer { [weak self] _ in
            DispatchQueue.main.async {
                self?.refreshPanelText()
                LicenseGate.shared.updateWindowCountdown()
            }
        }
    }

    private func closeSettingsSheet() {
        for w in NSApp.windows where injected.contains(ObjectIdentifier(w)) {
            if let parent = w.sheetParent { parent.endSheet(w) }
        }
        injected.removeAll()
    }
}
