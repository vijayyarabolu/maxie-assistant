import Foundation
import SQLite3

/// His Messages, read-only, from ~/Library/Messages/chat.db (needs Full Disk Access for Maxie; opt-in).
/// Nothing is written, copied or kept: each request opens the database read-only, reads the newest messages
/// from the handles that belong to one person, and closes it.
struct MessagesStore: Sendable {
    static let defaultPath = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Messages/chat.db").path
    static let appleEpoch: TimeInterval = 978_307_200   // 2001-01-01 in unix seconds
    static let maxRows = 500

    enum Failure: Error, Equatable { case noAccess, broken(String) }

    struct Message: Sendable, Equatable {
        let text: String
        let date: TimeInterval   // unix seconds
        let fromMe: Bool
        let handle: String
    }

    let path: String

    init(path: String = MessagesStore.defaultPath) { self.path = path }

    /// Newest first, from any of `handles` (phone numbers in any format, or emails), within `days`.
    func messages(from handles: [String], days: Int, now: Date = Date()) throws(Failure) -> [Message] {
        let db = try open()
        defer { sqlite3_close(db) }
        let wanted = Set(handles.map(Self.key))
        let ids = try rows(db, "SELECT ROWID, id FROM handle") { stmt in
            (sqlite3_column_int64(stmt, 0), Self.text(stmt, 1))
        }.filter { wanted.contains(Self.key($0.1)) }
        guard !ids.isEmpty else { return [] }
        let since = (now.timeIntervalSince1970 - Double(days) * 86_400 - Self.appleEpoch) * 1e9
        let list = ids.map { String($0.0) }.joined(separator: ",")
        let sql = """
            SELECT m.text, m.attributedBody, m.date, m.is_from_me, h.id FROM message m JOIN handle h ON h.ROWID = m.handle_id
            WHERE m.handle_id IN (\(list)) AND m.date > \(Int64(since)) ORDER BY m.date DESC LIMIT \(Self.maxRows)
            """
        return try rows(db, sql) { stmt in
            let plain = Self.text(stmt, 0)
            let body = plain.isEmpty ? (Self.blob(stmt, 1).flatMap(TypedStream.text) ?? "") : plain
            return Message(text: body, date: Self.unix(sqlite3_column_int64(stmt, 2)),
                           fromMe: sqlite3_column_int(stmt, 3) != 0, handle: Self.text(stmt, 4))
        }.filter { !$0.text.isEmpty }
    }

    /// Phones compare on their last 10 digits ("+1 (312) 555-0142" == "3125550142"), emails lower-cased.
    static func key(_ handle: String) -> String {
        if handle.contains("@") { return handle.lowercased() }
        let digits = handle.filter(\.isNumber)
        return String(digits.suffix(10))
    }

    /// chat.db stores nanoseconds since 2001 (seconds in very old databases).
    static func unix(_ raw: Int64) -> TimeInterval {
        let seconds = raw > 100_000_000_000 ? Double(raw) / 1e9 : Double(raw)
        return seconds + appleEpoch
    }

    private func open() throws(Failure) -> OpaquePointer {
        var db: OpaquePointer?
        let uri = "file:\(path)?mode=ro"
        guard sqlite3_open_v2(uri, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, nil) == SQLITE_OK, let db else {
            sqlite3_close(db)
            throw .noAccess
        }
        // Without Full Disk Access the open "works" and the first read fails: probe once.
        var stmt: OpaquePointer?
        let ok = sqlite3_prepare_v2(db, "SELECT 1 FROM handle LIMIT 1", -1, &stmt, nil) == SQLITE_OK
        sqlite3_finalize(stmt)
        guard ok else {
            sqlite3_close(db)
            throw .noAccess
        }
        return db
    }

    private func rows<T>(_ db: OpaquePointer, _ sql: String, _ row: (OpaquePointer) -> T) throws(Failure) -> [T] {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let stmt else {
            throw .broken(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        var out: [T] = []
        while sqlite3_step(stmt) == SQLITE_ROW { out.append(row(stmt)) }
        return out
    }

    private static func text(_ stmt: OpaquePointer, _ i: Int32) -> String {
        sqlite3_column_text(stmt, i).map { String(cString: $0) } ?? ""
    }

    private static func blob(_ stmt: OpaquePointer, _ i: Int32) -> Data? {
        guard let p = sqlite3_column_blob(stmt, i) else { return nil }
        return Data(bytes: p, count: Int(sqlite3_column_bytes(stmt, i)))
    }
}

/// The text inside an `attributedBody` (an NSAttributedString in NeXT "typedstream" form). Parsed by hand:
/// NSUnarchiver raises Objective-C exceptions on odd blobs, which would crash the app.
enum TypedStream {
    static func text(from data: Data) -> String? {
        let bytes = [UInt8](data)
        guard let at = bytes.firstRange(of: Array("NSString".utf8)) else { return nil }
        var i = at.upperBound
        while i < bytes.count, bytes[i] != 0x2B { i += 1 }   // '+' starts the string
        i += 1
        guard i < bytes.count else { return nil }
        var length = Int(bytes[i])
        i += 1
        if length == 0x81, i + 1 < bytes.count {             // 2-byte little-endian length
            length = Int(bytes[i]) | Int(bytes[i + 1]) << 8
            i += 2
        } else if length == 0x82, i + 3 < bytes.count {      // 4-byte little-endian length
            length = Int(bytes[i]) | Int(bytes[i + 1]) << 8 | Int(bytes[i + 2]) << 16 | Int(bytes[i + 3]) << 24
            i += 4
        }
        guard length > 0, i + length <= bytes.count else { return nil }
        return String(bytes: bytes[i..<i + length], encoding: .utf8)
    }
}
