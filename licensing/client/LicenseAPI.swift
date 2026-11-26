import Foundation

/// 与服务端通信。全部走 JSON，失败不抛异常给用户，只回调 Result。
enum LicenseAPI {

    enum APIError: Error, CustomStringConvertible {
        case network(String)
        case server(String)
        case badPayload

        var description: String {
            switch self {
            case .network(let m): return "网络异常：\(m)"
            case .server(let m): return m
            case .badPayload: return "服务端返回格式异常"
            }
        }
    }

    static var baseURL: URL {
        if let env = ProcessInfo.processInfo.environment["MS_LICENSE_SERVER"], !env.isEmpty,
           let u = URL(string: env) {
            return u
        }
        if let forced = LicenseConfig.forceServer, !forced.isEmpty, let u = URL(string: forced) {
            return u
        }
        return URL(string: LicenseConfig.serverBase) ?? URL(string: "http://127.0.0.1:8899")!
    }

    private static let session: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 8
        cfg.timeoutIntervalForResource = 12
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        cfg.httpAdditionalHeaders = ["User-Agent": "MagnetSearch/1.0 (macOS; license)"]
        return URLSession(configuration: cfg)
    }()

    // MARK: - 具体接口

    static func session(completion: @escaping (Result<[String: Any], Error>) -> Void) {
        post("/api/v1/session", [
            "device_id": DeviceIdentity.deviceID,
            "platform": DeviceIdentity.platformDescription,
            "app_version": DeviceIdentity.appVersion,
        ], completion: completion)
    }

    static func createOrder(completion: @escaping (Result<[String: Any], Error>) -> Void) {
        post("/api/v1/order/create", ["device_id": DeviceIdentity.deviceID], completion: completion)
    }

    /// 付费墙模式：取回「本机出口 IP 对应的那张未使用授权码」（付款后平台跳到领码页发的）
    static func claimAuto(completion: @escaping (Result<[String: Any], Error>) -> Void) {
        get("/api/v1/claim/auto?device_id=\(DeviceIdentity.deviceID)", completion: completion)
    }

    /// 付费墙模式：用一次性授权码兑换永久授权
    static func redeem(code: String, completion: @escaping (Result<[String: Any], Error>) -> Void) {
        post("/api/v1/redeem", [
            "device_id": DeviceIdentity.deviceID,
            "code": code,
        ], completion: completion)
    }

    static func orderStatus(_ orderID: String, completion: @escaping (Result<[String: Any], Error>) -> Void) {
        let q = "?order_id=\(orderID.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? orderID)"
            + "&device_id=\(DeviceIdentity.deviceID)"
        get("/api/v1/order/status" + q, completion: completion)
    }

    // MARK: - 传输

    private static func post(_ path: String, _ body: [String: Any],
                             completion: @escaping (Result<[String: Any], Error>) -> Void) {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            completion(.failure(APIError.network("地址无效")))
            return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body, options: [])
        send(req, completion: completion)
    }

    private static func get(_ path: String, completion: @escaping (Result<[String: Any], Error>) -> Void) {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            completion(.failure(APIError.network("地址无效")))
            return
        }
        send(URLRequest(url: url), completion: completion)
    }

    private static func send(_ req: URLRequest,
                             completion: @escaping (Result<[String: Any], Error>) -> Void) {
        session.dataTask(with: req) { data, resp, err in
            guard let data = data else {
                completion(.failure(APIError.network(err?.localizedDescription ?? "无响应")))
                return
            }
            guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
                completion(.failure(APIError.badPayload))
                return
            }
            let code = (obj["code"] as? Int) ?? -1
            let msg = (obj["msg"] as? String) ?? ""
            let http = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code != 0 || http >= 400 {
                completion(.failure(APIError.server(msg.isEmpty ? "HTTP \(http)" : msg)))
                return
            }
            completion(.success((obj["data"] as? [String: Any]) ?? [:]))
        }.resume()
    }
}
