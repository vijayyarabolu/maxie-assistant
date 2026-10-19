import Foundation
import Network

/// The "Apple bridge": Maxie.app answers the hub's questions about Messages, Calendar,
/// Contacts, Notes, Mail, Spotlight and Apple's on-device model, because macOS grants those to this app and
/// never to a child process like the Python hub. Plain HTTP/1.1 on 127.0.0.1:8783 only, every request needs
/// the hub token, one request per connection, bodies capped at 1 MB.
struct BridgeRequest: Sendable, Equatable {
    let method: String
    let path: String
    let query: [String: String]
    let headers: [String: String]   // lower-cased names
    let body: Data

    func json() -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
    }

    /// Parses one request; nil until the head and the whole body (Content-Length) have arrived.
    static func parse(_ data: Data) -> BridgeRequest? {
        guard let headEnd = data.range(of: Data("\r\n\r\n".utf8)),
              let head = String(data: data[..<headEnd.lowerBound], encoding: .utf8) else { return nil }
        var lines = head.components(separatedBy: "\r\n")
        let start = lines.removeFirst().split(separator: " ")
        guard start.count >= 2 else { return nil }
        var headers: [String: String] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { continue }
            headers[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length = Int(headers["content-length"] ?? "0") ?? 0
        let body = data[headEnd.upperBound...]
        guard body.count >= length else { return nil }
        let comps = URLComponents(string: String(start[1]))
        var query: [String: String] = [:]
        for item in comps?.queryItems ?? [] { query[item.name] = item.value ?? "" }
        return BridgeRequest(method: String(start[0]), path: comps?.path ?? "/", query: query,
                             headers: headers, body: Data(body.prefix(length)))
    }
}

struct BridgeResponse: Sendable {
    let status: Int
    let body: Data

    static func json(_ object: Any, status: Int = 200) -> BridgeResponse {
        BridgeResponse(status: status, body: (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8))
    }

    /// The hub turns `error` into a sentence for the user ("turn on Full Disk Access for Maxie…").
    static func error(_ reason: String, status: Int = 403) -> BridgeResponse { json(["error": reason], status: status) }

    var wire: Data {
        let reason = [200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
                      413: "Payload Too Large", 500: "Internal Server Error", 503: "Service Unavailable"][status] ?? "Error"
        var out = Data("HTTP/1.1 \(status) \(reason)\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8)
        out.append(body)
        return out
    }
}

final class BridgeServer: @unchecked Sendable {
    static let port: UInt16 = 8783
    static let maxBytes = 1 << 20

    private let token: String
    private let handle: @Sendable (BridgeRequest) async -> BridgeResponse
    private let queue = DispatchQueue(label: "maxie.bridge")
    private var listener: NWListener?

    init(token: String, handle: @escaping @Sendable (BridgeRequest) async -> BridgeResponse) {
        self.token = token
        self.handle = handle
    }

    func start() {
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: Self.port)!)
        params.allowLocalEndpointReuse = true
        guard let l = try? NWListener(using: params) else {
            Log.d("bridge: can't listen on \(Self.port)")
            return
        }
        l.newConnectionHandler = { [weak self] conn in self?.serve(conn) }
        l.stateUpdateHandler = { state in if case .failed(let e) = state { Log.d("bridge: \(e)") } }
        l.start(queue: queue)
        listener = l
    }

    func stop() { listener?.cancel() }

    /// Bearer token, compared in constant time.
    func authorized(_ r: BridgeRequest) -> Bool {
        let given = Array((r.headers["authorization"] ?? "").utf8), want = Array("Bearer \(token)".utf8)
        guard !token.isEmpty, given.count == want.count else { return false }
        return zip(given, want).reduce(0) { $0 | ($1.0 ^ $1.1) } == 0
    }

    private func serve(_ conn: NWConnection) {
        conn.start(queue: queue)
        receive(conn, Data())
    }

    private func receive(_ conn: NWConnection, _ buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] chunk, _, done, error in
            guard let self else { return }
            var data = buffer
            if let chunk { data.append(chunk) }
            if data.count > Self.maxBytes { return self.reply(conn, .error("too_large", status: 413)) }
            if let req = BridgeRequest.parse(data) {
                guard self.authorized(req) else { return self.reply(conn, .error("unauthorized", status: 401)) }
                let handle = self.handle
                Task { self.reply(conn, await handle(req)) }
            } else if done || error != nil {
                conn.cancel()
            } else {
                self.receive(conn, data)
            }
        }
    }

    private func reply(_ conn: NWConnection, _ r: BridgeResponse) {
        conn.send(content: r.wire, completion: .contentProcessed { _ in conn.cancel() })
    }
}
