import Foundation
import AppKit
import Vision

/// 付费墙模式下的二维码解析：
/// 平台那张「付费图」左下角其实是一个收款链接的二维码，把它解出来直接渲染到我们弹窗里，
/// 买家扫码 → 支付宝付款 → 平台跳转到我们的授权页。
///
/// 为什么每次都要现取：链接里的 `t=YYYY_MM_DD_HH` 是时间桶，取当次页面的最新链接最稳妥；
/// 取不到就退回用页地址本身（也能用，只是多一次网页跳转）。
enum PaywallQR {

    struct Result {
        var payload: String
        var isDirect: Bool        // true = 直接解出的收款链接；false = 退回页地址
    }

    static func resolve(pageURL: String, fallback: String, completion: @escaping (Result) -> Void) {
        guard let url = URL(string: pageURL) else {
            completion(Result(payload: fallback, isDirect: false))
            return
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        req.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
                     + "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                     forHTTPHeaderField: "User-Agent")
        URLSession(configuration: .ephemeral).dataTask(with: req) { data, _, _ in
            guard let data = data,
                  let html = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1)
            else {
                DispatchQueue.main.async { completion(Result(payload: fallback, isDirect: false)) }
                return
            }
            guard let imgData = extractBase64Image(html),
                  let payload = decodeQR(imgData), !payload.isEmpty else {
                DispatchQueue.main.async { completion(Result(payload: fallback, isDirect: false)) }
                return
            }
            NSLog("[LIC] 从付费页解出收款链接：%@", payload)
            DispatchQueue.main.async { completion(Result(payload: payload, isDirect: true)) }
        }.resume()
    }

    private static func extractBase64Image(_ html: String) -> Data? {
        guard let re = try? NSRegularExpression(
                pattern: "data:image/(?:jpeg|jpg|png);base64,([A-Za-z0-9+/=\\s]+?)\"",
                options: []) else { return nil }
        let ns = html as NSString
        guard let m = re.firstMatch(in: html, options: [], range: NSRange(location: 0, length: ns.length)),
              m.numberOfRanges > 1 else { return nil }
        var b64 = ns.substring(with: m.range(at: 1))
        b64 = b64.components(separatedBy: .whitespacesAndNewlines).joined()
        return Data(base64Encoded: b64)
    }

    private static func decodeQR(_ data: Data) -> String? {
        guard let img = NSImage(data: data),
              let tiff = img.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let cg = rep.cgImage else { return nil }
        // 原图可能很小（251×290），先放大 4 倍再识别
        let scale = 4
        let w = cg.width * scale, h = cg.height * scale
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            return detect(cg)
        }
        ctx.interpolationQuality = .none
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        if let big = ctx.makeImage(), let s = detect(big) { return s }
        return detect(cg)
    }

    private static func detect(_ cg: CGImage) -> String? {
        let req = VNDetectBarcodesRequest()
        req.symbologies = [.qr]
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        try? handler.perform([req])
        for r in (req.results ?? []) {
            if let s = r.payloadStringValue, !s.isEmpty { return s }
        }
        return nil
    }
}
