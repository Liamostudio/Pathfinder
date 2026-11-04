import Foundation

struct GatewayStatus: Decodable {
    let available: Bool
    let model: String
    let voice_mode: String
    let simulator_only: Bool
}
struct CommandResult: Decodable {
    let command: String
    let reply: String
    let error: String
}
struct InputJob: Decodable {
    let id: String?
    let job_id: String?
    let status: String
    let transcript: String?
    let error: String?
    let result: CommandResult?
    var identifier: String { job_id ?? id ?? "" }
}
struct GatewayError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct GatewayClient {
    let base: URL
    let token: String
    private let session: URLSession

    init(address: String, token: String) throws {
        guard let url = URL(string: address), let host = url.host,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/",
              url.scheme == "https" || (url.scheme == "http" && ["localhost", "127.0.0.1"].contains(host)) else {
            throw GatewayError(message: "Use the HTTPS gateway address from the laptop.")
        }
        guard token.count >= 24 else { throw GatewayError(message: "Enter the gateway pairing token, not a Meta API key.") }
        self.base = url; self.token = token
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 12
        config.timeoutIntervalForResource = 20
        config.urlCache = nil
        session = URLSession(configuration: config)
    }

    func call<T: Decodable>(_ path: String, body: [String: Any]? = nil, timeout: TimeInterval = 12) async throws -> T {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body { request.httpMethod = "POST"; request.httpBody = try JSONSerialization.data(withJSONObject: body) }
        // Default URLSession trust evaluation: no self-signed-certificate bypass.
        let (data, response) = try await session.data(for: request)
        guard data.count < 3 * 1024 * 1024 else { throw GatewayError(message: "Gateway reply is too large.") }
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let object = try? JSONSerialization.jsonObject(with: data)
            let error = (object as? [String: Any])?["error"] as? String
            throw GatewayError(message: error ?? "The gateway refused the request.")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
    func status() async throws -> GatewayStatus { try await call("v1/status") }
    func job(_ id: String) async throws -> InputJob { try await call("v1/jobs/\(id)") }
    func stop() async throws -> InputJob { try await call("v1/stop", body: [:], timeout: 3) }
}
