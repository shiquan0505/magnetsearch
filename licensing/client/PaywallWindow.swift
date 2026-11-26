import AppKit
import CoreImage

struct PaywallContent {
    var headline: String = "MagnetSearch 试用已结束"
    var subline: String = "扫码付款，本机立即永久解锁 · 无需卡密"
    var qrImage: NSImage?
    var footer: String = ""
    var status: String = ""
    var showBuyButtons: Bool = true
    var showOpenPage: Bool = true
    var refreshTitle: String = " 我已付款，立即刷新 "
    /// 付费墙模式：显示「粘贴授权码」输入框
    var showCodeEntry: Bool = false
}

/// 收银台弹窗（软件内弹窗，不是全屏遮罩）。
///
/// 优先作为 **sheet** 挂到 app 主窗口上：窗口内交互被挡住、不可随手关掉，
/// 但视觉上就是一个正常的对话框；找不到宿主窗口（例如主窗口还没出来）时，
/// 退化成居中的小浮动窗。高度随内容自适应，离线/停用状态不显示二维码时会更矮。
final class PaywallWindow: NSObject, NSWindowDelegate {

    private var window: NSWindow?
    private var hostWindow: NSWindow?
    private var card: NSView!
    private var stack: NSStackView!
    private var qrView: NSImageView!
    private var qrBox: NSView!
    private var statusLabel: NSTextField!
    private var footerLabel: NSTextField!
    private var headlineLabel: NSTextField!
    private var sublineLabel: NSTextField!
    private var refreshButton: NSButton!
    private var openButton: NSButton!
    private var codeRow: NSStackView!
    private var codeField: NSTextField!
    private var retries = 0

    private let contentWidth: CGFloat = 460
    private let sidePadding: CGFloat = 30
    private let verticalPadding: CGFloat = 26
    private let qrSide: CGFloat = 196

    var onRefresh: (() -> Void)?
    var onOpenPayPage: (() -> Void)?
    var onQuit: (() -> Void)?
    var onRedeem: ((String) -> Void)?

    var isVisible: Bool { window?.isVisible ?? false }

    static weak var currentWindow: NSWindow?

    static func isPaywallWindow(_ w: NSWindow) -> Bool {
        if let cur = currentWindow, cur === w { return true }
        return w.title == "购买授权" || w.title.contains("试用") || w.title.contains("已授权")
    }

    // MARK: - 展示

    func show(content: PaywallContent) {
        assert(Thread.isMainThread)
        if window == nil { build() }
        apply(content)
        present()
    }

    func updateStatus(_ text: String) {
        DispatchQueue.main.async {
            self.statusLabel?.stringValue = text
        }
    }

    func apply(_ c: PaywallContent) {
        guard window != nil else { return }
        headlineLabel.stringValue = c.headline
        sublineLabel.stringValue = c.subline
        statusLabel.stringValue = c.status
        footerLabel.stringValue = c.footer
        refreshButton.isHidden = !c.showBuyButtons
        openButton.isHidden = !(c.showBuyButtons && c.showOpenPage)
        refreshButton.title = c.refreshTitle
        codeRow.isHidden = !c.showCodeEntry
        if let img = c.qrImage {
            qrView.image = img
            qrBox.isHidden = false
        } else {
            qrBox.isHidden = true
        }
        window?.title = c.headline
        resizeToFit()
    }

    /// 关掉弹窗：sheet 走 endSheet，普通窗走 orderOut。
    /// 绝不 close()/置空，避免程序化窗口释放后主线程再碰导致崩溃。
    func close() {
        DispatchQueue.main.async {
            guard let w = self.window else { return }
            if let parent = w.sheetParent {
                parent.endSheet(w)
            }
            w.orderOut(nil)
        }
    }

    // MARK: - 挂到宿主窗口

    private func present() {
        guard let w = window else { return }
        if let host = findHostWindow() {
            hostWindow = host
            retries = 0
            if w.sheetParent !== host {
                if let old = w.sheetParent { old.endSheet(w) }
                host.beginSheet(w, completionHandler: nil)
            } else if !w.isVisible {
                w.orderOut(nil)
                host.beginSheet(w, completionHandler: nil)
            }
            return
        }
        // 没有宿主窗口：居中浮窗，稍后再试一次挂 sheet（app 可能刚启动还没开窗）
        centerOnScreen()
        w.makeKeyAndOrderFront(nil)
        if w.sheetParent == nil { NSApp.activate(ignoringOtherApps: false) }
        if retries < 6 {
            retries += 1
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
                guard let self = self, let win = self.window, win.sheetParent == nil,
                      self.isVisible else { return }
                if let host = self.findHostWindow() {
                    self.hostWindow = host
                    win.orderOut(nil)
                    host.beginSheet(win, completionHandler: nil)
                } else {
                    self.present()
                }
            }
        }
    }

    /// 找一个可用的 app 窗口当宿主：可见、能当 key、不是我们自己、不是别的 sheet。
    private func findHostWindow() -> NSWindow? {
        let all = NSApp.windows.filter { w in
            w !== self.window && w.isVisible && w.canBecomeKey && !(w is NSPanel)
                && w.frame.width > 320 && w.frame.height > 240
        }
        // 优先主窗口，其次任取一个
        let main = all.first { $0.isMainWindow } ?? NSApp.mainWindow.flatMap { m in
            all.contains(where: { $0 === m }) ? m : nil
        }
        return main ?? all.first
    }

    private func centerOnScreen() {
        guard let w = window else { return }
        let visible = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        let size = w.frame.size
        w.setFrameOrigin(NSPoint(x: visible.midX - size.width / 2,
                                 y: visible.midY - size.height / 2 + 40))
    }

    private func resizeToFit() {
        guard let w = window, let s = stack else { return }
        let h = s.fittingSize.height + verticalPadding * 2
        w.setContentSize(NSSize(width: contentWidth, height: max(240, h)))
    }

    // MARK: - 构建 UI

    private func build() {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: contentWidth, height: 560),
                         styleMask: [.titled, .fullSizeContentView],
                         backing: .buffered,
                         defer: false)
        w.title = "购买授权"
        w.titleVisibility = .hidden
        w.titlebarAppearsTransparent = true
        w.isMovableByWindowBackground = true
        w.backgroundColor = .clear
        w.isOpaque = false
        w.hasShadow = true
        w.isReleasedWhenClosed = false
        w.level = .normal
        w.collectionBehavior = [.fullScreenAuxiliary]
        w.delegate = self
        [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton].forEach {
            w.standardWindowButton($0)?.isHidden = true
        }
        window = w

        let card = NSView(frame: w.contentView?.bounds ?? .zero)
        card.wantsLayer = true
        card.layer?.backgroundColor = NSColor(calibratedWhite: 0.11, alpha: 1).cgColor
        card.layer?.cornerRadius = 14
        card.layer?.borderWidth = 1
        card.layer?.borderColor = NSColor(calibratedWhite: 0.26, alpha: 1).cgColor
        card.autoresizingMask = [.width, .height]
        w.contentView = card
        self.card = card

        let sv = NSStackView()
        sv.orientation = .vertical
        sv.alignment = .centerX
        sv.spacing = 12
        sv.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(sv)
        NSLayoutConstraint.activate([
            sv.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: sidePadding),
            sv.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -sidePadding),
            sv.topAnchor.constraint(equalTo: card.topAnchor, constant: verticalPadding),
            sv.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -verticalPadding),
        ])
        stack = sv

        headlineLabel = makeLabel("MagnetSearch 试用已结束", size: 17, weight: .semibold,
                                  color: NSColor(calibratedWhite: 0.96, alpha: 1))
        sublineLabel = makeLabel("扫码付款，本机立即永久解锁 · 无需卡密", size: 12, weight: .regular,
                                 color: NSColor(calibratedWhite: 0.68, alpha: 1))
        sv.addArrangedSubview(headlineLabel)
        sv.addArrangedSubview(sublineLabel)

        // 二维码白底
        let box = NSView()
        box.wantsLayer = true
        box.layer?.backgroundColor = NSColor.white.cgColor
        box.layer?.cornerRadius = 10
        box.translatesAutoresizingMaskIntoConstraints = false
        box.widthAnchor.constraint(equalToConstant: qrSide + 20).isActive = true
        box.heightAnchor.constraint(equalToConstant: qrSide + 20).isActive = true

        qrView = NSImageView()
        qrView.imageScaling = .scaleProportionallyUpOrDown
        qrView.translatesAutoresizingMaskIntoConstraints = false
        box.addSubview(qrView)
        NSLayoutConstraint.activate([
            qrView.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 10),
            qrView.trailingAnchor.constraint(equalTo: box.trailingAnchor, constant: -10),
            qrView.topAnchor.constraint(equalTo: box.topAnchor, constant: 10),
            qrView.bottomAnchor.constraint(equalTo: box.bottomAnchor, constant: -10),
        ])
        qrBox = box
        sv.addArrangedSubview(box)
        sv.setCustomSpacing(14, after: sublineLabel)
        sv.setCustomSpacing(14, after: box)

        statusLabel = makeLabel("正在生成支付二维码…", size: 12, weight: .medium,
                                color: NSColor(calibratedWhite: 0.82, alpha: 1))
        sv.addArrangedSubview(statusLabel)

        footerLabel = makeLabel("", size: 10.5, weight: .regular, color: NSColor(calibratedWhite: 0.48, alpha: 1))
        footerLabel.isSelectable = true
        footerLabel.lineBreakMode = .byTruncatingMiddle
        footerLabel.alignment = .center
        sv.addArrangedSubview(footerLabel)

        refreshButton = NSButton(title: " 我已付款，立即刷新 ", target: self, action: #selector(doRefresh))
        refreshButton.bezelStyle = .rounded
        refreshButton.keyEquivalent = "\r"
        refreshButton.controlSize = .large
        openButton = NSButton(title: " 打开支付页面 ", target: self, action: #selector(doOpen))
        openButton.bezelStyle = .rounded
        openButton.controlSize = .large
        let quitButton = NSButton(title: " 退出 ", target: self, action: #selector(doQuit))
        quitButton.bezelStyle = .rounded
        quitButton.controlSize = .large

        let row = NSStackView(views: [refreshButton, openButton, quitButton])
        row.orientation = .horizontal
        row.spacing = 8
        sv.addArrangedSubview(row)

        // 授权码行（付费墙模式）
        codeField = NSTextField()
        codeField.placeholderString = "ABC-DEF"
        codeField.font = NSFont.monospacedSystemFont(ofSize: 15, weight: .semibold)
        codeField.alignment = .center
        codeField.translatesAutoresizingMaskIntoConstraints = false
        codeField.widthAnchor.constraint(equalToConstant: 130).isActive = true
        codeField.target = self
        codeField.action = #selector(doRedeemFromField)
        let redeemButton = NSButton(title: " 开通 ", target: self, action: #selector(doRedeem))
        redeemButton.bezelStyle = .rounded
        redeemButton.controlSize = .large
        let prompt = NSTextField(labelWithString: "授权码")
        prompt.font = NSFont.systemFont(ofSize: 12)
        prompt.textColor = NSColor(calibratedWhite: 0.6, alpha: 1)

        codeRow = NSStackView(views: [prompt, codeField, redeemButton])
        codeRow.orientation = .horizontal
        codeRow.spacing = 8
        codeRow.isHidden = true
        sv.addArrangedSubview(codeRow)

        resizeToFit()
    }

    private func makeLabel(_ text: String, size: CGFloat, weight: NSFont.Weight,
                           color: NSColor) -> NSTextField {
        let l = NSTextField(wrappingLabelWithString: text)
        l.font = NSFont.systemFont(ofSize: size, weight: weight)
        l.textColor = color
        l.alignment = .center
        l.isEditable = false
        l.isBordered = false
        l.drawsBackground = false
        l.maximumNumberOfLines = 2
        l.preferredMaxLayoutWidth = contentWidth - sidePadding * 2
        return l
    }

    // MARK: - 事件

    @objc private func doRefresh() { onRefresh?() }

    @objc private func doRedeem() { submitCode() }

    @objc private func doRedeemFromField() { submitCode() }

    private func submitCode() {
        let raw = codeField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else {
            NSSound.beep()
            return
        }
        onRedeem?(raw)
    }
    @objc private func doOpen() { onOpenPayPage?() }
    @objc private func doQuit() { onQuit?() }

    func windowShouldClose(_ sender: NSWindow) -> Bool { false }

    // MARK: - 二维码

    static func qrImage(_ text: String, size: CGFloat = 196) -> NSImage? {
        guard let data = text.data(using: .utf8),
              let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        filter.setValue(data, forKey: "inputMessage")
        filter.setValue("M", forKey: "inputCorrectionLevel")
        guard let out = filter.outputImage else { return nil }
        let scale = size / max(out.extent.width, 1)
        let scaled = out.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        let rep = NSCIImageRep(ciImage: scaled)
        let img = NSImage(size: NSSize(width: rep.pixelsWide, height: rep.pixelsHigh))
        img.addRepresentation(rep)
        return img
    }
}
