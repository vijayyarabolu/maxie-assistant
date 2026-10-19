import SQLite3
import XCTest
@testable import ClaudeNotch

/// The Apple bridge: request parsing, the token, chat.db reading with a synthetic database.
final class BridgeTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appending(path: "bridge-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    /// "Maxie, look" from the voice helper; the hub asks Maxie.app (it holds Screen Recording).
    func testTheScreenRouteHandsOverOneScreenshot() async throws {
        let r = await BridgeRoutes.screen(grab: { "data:image/jpeg;base64,AAAA" })
        XCTAssertEqual(r.status, 200)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: r.body) as? [String: String])
        XCTAssertEqual(body["image"], "data:image/jpeg;base64,AAAA")
    }

    func testNoScreenRecordingSaysSoInTheHubsWord() async throws {
        let r = await BridgeRoutes.screen(grab: { throw ScreenCapture.Failure.noPermission })
        XCTAssertEqual(r.status, 403)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: r.body) as? [String: String])
        XCTAssertEqual(body["error"], "screen", "the hub's WHY[\"screen\"]: turn on Screen Recording")
        let other = await BridgeRoutes.screen(grab: { throw ScreenCapture.Failure.noDisplay })
        XCTAssertEqual(other.status, 500)
    }

    func testParsesARequestOnlyOnceTheBodyIsComplete() {
        let head = "POST /fm?x=1 HTTP/1.1\r\nAuthorization: Bearer abc\r\nContent-Length: 9\r\n\r\n"
        XCTAssertNil(BridgeRequest.parse(Data((head + "{\"a\":").utf8)))
        let r = BridgeRequest.parse(Data((head + "{\"a\":\"b\"}").utf8))
        XCTAssertEqual(r?.method, "POST")
        XCTAssertEqual(r?.path, "/fm")
        XCTAssertEqual(r?.query["x"], "1")
        XCTAssertEqual(r?.headers["authorization"], "Bearer abc")
        XCTAssertEqual(r?.json()["a"] as? String, "b")
    }

    func testOnlyTheHubTokenGetsIn() {
        let server = BridgeServer(token: "s3cret") { _ in .json([:]) }
        func req(_ auth: String) -> BridgeRequest {
            BridgeRequest.parse(Data("GET /health HTTP/1.1\r\nAuthorization: \(auth)\r\n\r\n".utf8))!
        }
        XCTAssertTrue(server.authorized(req("Bearer s3cret")))
        XCTAssertFalse(server.authorized(req("Bearer s3cres")))
        XCTAssertFalse(server.authorized(req("")))
        XCTAssertFalse(BridgeServer(token: "") { _ in .json([:]) }.authorized(req("Bearer ")))
    }

    func testReadsTextHiddenInAttributedBody() {
        XCTAssertEqual(TypedStream.text(from: Self.blob("Meet at 55 W 3rd Avenue")), "Meet at 55 W 3rd Avenue")
        let long = String(repeating: "a", count: 300)
        XCTAssertEqual(TypedStream.text(from: Self.blob(long)), long)
        XCTAssertNil(TypedStream.text(from: Data("garbage".utf8)))
    }

    func testHandlesMatchWhateverTheFormat() {
        XCTAssertEqual(MessagesStore.key("+1 (312) 555-0142"), MessagesStore.key("3125550142"))
        XCTAssertEqual(MessagesStore.key("Riley@Example.com"), "riley@example.com")
        XCTAssertEqual(MessagesStore.unix(0), MessagesStore.appleEpoch)
        XCTAssertEqual(MessagesStore.unix(1_000_000_000_000), MessagesStore.appleEpoch + 1000, accuracy: 0.001)
    }

    func testFindsOnePersonsMessagesNewestFirstByContactName() throws {
        // Arrange: Riley (two handles), a stranger, and one message too old to count
        let path = try makeChatDB(rows: [
            (handle: 1, text: "Dinner Saturday?", body: nil, daysAgo: 1, fromMe: false),
            (handle: 2, text: nil, body: "My new place is 1234 Oak St, Springfield, IL 62704", daysAgo: 12, fromMe: false),
            (handle: 1, text: "see you", body: nil, daysAgo: 13, fromMe: true),
            (handle: 3, text: "spam 99999", body: nil, daysAgo: 2, fromMe: false),
            (handle: 1, text: "ancient", body: nil, daysAgo: 800, fromMe: false),
        ])
        let riley = ContactsStore.Person(name: "Riley Reyes", phones: ["(312) 555-0142"], emails: ["riley@example.com"])

        // Act
        let rows = try BridgeRoutes.messages(person: "Riley", query: "", days: 365, store: MessagesStore(path: path),
                                             lookup: { _ throws(AppleError) in [riley] })

        // Assert
        XCTAssertEqual(rows.map { $0["text"] as? String },
                       ["Dinner Saturday?", "My new place is 1234 Oak St, Springfield, IL 62704", "see you"])
        XCTAssertEqual(rows.first?["sender"] as? String, "Riley Reyes")
        XCTAssertEqual(rows.last?["from_me"] as? Bool, true)
    }

    func testAPhoneNumberNeedsNoContactsAndQueryFilters() throws {
        let path = try makeChatDB(rows: [
            (handle: 1, text: "gate code 4821", body: nil, daysAgo: 1, fromMe: false),
            (handle: 1, text: "hello", body: nil, daysAgo: 2, fromMe: false),
        ])
        let rows = try BridgeRoutes.messages(person: "312-555-0142", query: "CODE", days: 30, store: MessagesStore(path: path),
                                             lookup: { _ throws(AppleError) in throw .denied("contacts") })
        XCTAssertEqual(rows.map { $0["text"] as? String }, ["gate code 4821"])
    }

    func testNoFullDiskAccessIsReportedNotCrashed() {
        let store = MessagesStore(path: dir.appending(path: "missing.db").path)
        XCTAssertThrowsError(try store.messages(from: ["3125550142"], days: 30)) { e in
            XCTAssertEqual(e as? MessagesStore.Failure, .noAccess)
        }
    }

    func testUnknownRouteIs404AndHealthIsOK() async {
        let health = await BridgeRoutes.handle(BridgeRequest.parse(Data("GET /health HTTP/1.1\r\n\r\n".utf8))!)
        XCTAssertEqual(health.status, 200)
        let missing = await BridgeRoutes.handle(BridgeRequest.parse(Data("GET /nope HTTP/1.1\r\n\r\n".utf8))!)
        XCTAssertEqual(missing.status, 404)
        let bad = await BridgeRoutes.handle(BridgeRequest.parse(Data("POST /mail/draft HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}".utf8))!)
        XCTAssertEqual(bad.status, 400)
    }

    // MARK: fixtures

    /// The shape Messages uses: …NSString, then '+', a length byte (0x81 + 2 bytes when long), UTF-8.
    static func blob(_ s: String) -> Data {
        var d = Data([0x04, 0x0B]) + Data("streamtyped".utf8) + Data([0x81, 0xE8, 0x03, 0x84, 0x01, 0x40, 0x84, 0x84, 0x84])
        d += Data([0x12]) + Data("NSAttributedString".utf8) + Data([0x00, 0x84, 0x84, 0x08]) + Data("NSObject".utf8)
        d += Data([0x00, 0x85, 0x92, 0x84, 0x84, 0x84, 0x08]) + Data("NSString".utf8) + Data([0x01, 0x94, 0x84, 0x01, 0x2B])
        let bytes = Data(s.utf8)
        d += bytes.count < 0x80 ? Data([UInt8(bytes.count)]) : Data([0x81, UInt8(bytes.count & 0xFF), UInt8(bytes.count >> 8)])
        return d + bytes + Data([0x86, 0x84])
    }

    private func makeChatDB(rows: [(handle: Int, text: String?, body: String?, daysAgo: Double, fromMe: Bool)]) throws -> String {
        let path = dir.appending(path: "chat.db").path
        var db: OpaquePointer?
        XCTAssertEqual(sqlite3_open(path, &db), SQLITE_OK)
        defer { sqlite3_close(db) }
        let schema = """
            CREATE TABLE handle(ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE message(ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER, is_from_me INTEGER, handle_id INTEGER);
            INSERT INTO handle VALUES (1, '+13125550142'), (2, 'riley@example.com'), (3, '+18005550199');
            """
        XCTAssertEqual(sqlite3_exec(db, schema, nil, nil, nil), SQLITE_OK)
        let now = Date().timeIntervalSince1970
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        for r in rows {
            var stmt: OpaquePointer?
            sqlite3_prepare_v2(db, "INSERT INTO message(text, attributedBody, date, is_from_me, handle_id) VALUES (?,?,?,?,?)", -1, &stmt, nil)
            if let t = r.text { sqlite3_bind_text(stmt, 1, t, -1, transient) } else { sqlite3_bind_null(stmt, 1) }
            if let b = r.body {
                let data = Self.blob(b)
                _ = data.withUnsafeBytes { sqlite3_bind_blob(stmt, 2, $0.baseAddress, Int32(data.count), transient) }
            } else { sqlite3_bind_null(stmt, 2) }
            sqlite3_bind_int64(stmt, 3, Int64((now - r.daysAgo * 86_400 - MessagesStore.appleEpoch) * 1e9))
            sqlite3_bind_int(stmt, 4, r.fromMe ? 1 : 0)
            sqlite3_bind_int(stmt, 5, Int32(r.handle))
            XCTAssertEqual(sqlite3_step(stmt), SQLITE_DONE)
            sqlite3_finalize(stmt)
        }
        return path
    }
}
