import Foundation
import CryptoKit

/// 授权凭证：MS1.<base64url(payload)>.<base64url(sig)>
/// 用内嵌公钥做 Ed25519 验签 —— 客户端只能验、不能签，断网也能判断。
enum LicenseToken {

    struct Payload {
        var version: Int = 1
        var typ: String = ""            // license / trial
        var app: String = ""
        var deviceID: String = ""
        var plan: String?
        var issuedAt: Int?
        var expiresAt: Int?             // nil = 永久
        var trialStart: Int?
        var trialEnd: Int?
        var jti: String?
        var keyID: String?
    }

    enum TokenError: Error, CustomStringConvertible {
        case malformed
        case badHeader
        case badSignature
        case badPayload
        case wrongApp
        case wrongDevice
        case expired

        var description: String {
            switch self {
            case .malformed: return "token 格式错误"
            case .badHeader: return "token 头错误"
            case .badSignature: return "签名无效"
            case .badPayload: return "token 内容损坏"
            case .wrongApp: return "token 不属于本产品"
            case .wrongDevice: return "token 与当前设备不匹配"
            case .expired: return "授权已过期"
            }
        }
    }

    static func b64urlDecode(_ s: String) -> Data? {
        var t = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while t.count % 4 != 0 { t += "=" }
        return Data(base64Encoded: t)
    }

    static func verify(_ token: String, expectingDevice deviceID: String? = nil,
                       now: Date = Date()) throws -> Payload {
        let parts = token.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: ".",
                                                                                omittingEmptySubsequences: false)
        guard parts.count == 3 else { throw TokenError.malformed }
        guard parts[0] == "MS1" else { throw TokenError.badHeader }

        let signed = "\(parts[0]).\(parts[1])".data(using: .utf8)!
        guard let sig = b64urlDecode(String(parts[2])) else { throw TokenError.malformed }
        guard let pkData = Data(hexString: LicenseConfig.publicKeyHex),
              let pub = try? Curve25519.Signing.PublicKey(rawRepresentation: pkData) else {
            throw TokenError.badSignature
        }
        guard pub.isValidSignature(sig, for: signed) else { throw TokenError.badSignature }

        guard let bodyData = b64urlDecode(String(parts[1])),
              let obj = (try? JSONSerialization.jsonObject(with: bodyData)) as? [String: Any] else {
            throw TokenError.badPayload
        }
        guard let typ = obj["typ"] as? String else { throw TokenError.badPayload }

        var p = Payload()
        p.typ = typ
        p.version = (obj["v"] as? Int) ?? 1
        p.app = (obj["app"] as? String) ?? ""
        p.deviceID = (obj["did"] as? String) ?? ""
        p.plan = obj["plan"] as? String
        p.issuedAt = obj["iat"] as? Int
        p.expiresAt = obj["exp"] as? Int
        p.trialStart = obj["ts"] as? Int
        p.trialEnd = obj["te"] as? Int
        p.jti = obj["jti"] as? String
        p.keyID = obj["kid"] as? String

        if !p.app.isEmpty && p.app != LicenseConfig.appID { throw TokenError.wrongApp }
        if let want = deviceID, !p.deviceID.isEmpty, p.deviceID != want { throw TokenError.wrongDevice }
        if let exp = p.expiresAt, exp < Int(now.timeIntervalSince1970) { throw TokenError.expired }
        return p
    }
}

extension Data {
    init?(hexString: String) {
        let s = hexString.trimmingCharacters(in: .whitespacesAndNewlines)
        guard s.count % 2 == 0 else { return nil }
        var out = [UInt8]()
        out.reserveCapacity(s.count / 2)
        var idx = s.startIndex
        while idx < s.endIndex {
            let next = s.index(idx, offsetBy: 2)
            guard let b = UInt8(s[idx..<next], radix: 16) else { return nil }
            out.append(b)
            idx = next
        }
        self = Data(out)
    }
}
