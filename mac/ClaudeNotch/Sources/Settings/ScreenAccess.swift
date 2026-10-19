import Foundation
import Security
import SQLite3

/// Screen Recording's next step. macOS shows its prompt once per app identity; after that,
/// CGRequestScreenCaptureAccess does nothing, so "Turn on" was dead after the new signature. The row now opens
/// System Settings › Privacy & Security › Screen & System Audio Recording and says what to do there, then offers
/// "Reopen Maxie" (macOS applies the switch only to a fresh launch).
enum ScreenAccess {
    /// Maxie's row in macOS's own list: switched on or off, for this build or an older one.
    enum Entry: Equatable, Sendable { case mine(allowed: Bool), older(allowed: Bool) }

    enum Advice: Equatable, Sendable { case allowed, turnOn, turnOnInSettings, reopen, removeOld }

    static let line = "Turn on Maxie here, then reopen Maxie."
    /// Set when "Turn on" had to send the user to System Settings; cleared once it works.
    static let sentKey = "screenSentToSettings"
    /// Screen Recording lives in the system TCC database. Readable only with Full Disk Access; nil otherwise.
    static let systemDB = URL(fileURLWithPath: "/Library/Application Support/com.apple.TCC/TCC.db")
    private static let service = "kTCCServiceScreenCapture"
    private static let allowedValue: Int32 = 2

    static func advice(granted: Bool, sentToSettings: Bool, entry: Entry?) -> Advice {
        if granted { return .allowed }
        switch entry {
        case .mine(allowed: true): return .reopen
        case .mine(allowed: false): return .turnOnInSettings
        case .older: return .removeOld
        case nil: return sentToSettings ? .turnOnInSettings : .turnOn
        }
    }

    /// Maxie's Screen Recording row, if macOS's list can be read and has one. Whose it is: the code requirement
    /// macOS stored with it either matches this build or an older signature's.
    static func entry(db: URL = systemDB, client: String = Bundle.main.bundleIdentifier ?? "",
                      satisfies: (Data) -> Bool = selfSatisfies) -> Entry? {
        var h: OpaquePointer?
        defer { sqlite3_close(h) }
        guard sqlite3_open_v2(db.path, &h, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else { return nil }
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = "SELECT auth_value, csreq FROM access WHERE service = ?1 AND client = ?2 LIMIT 1"
        guard sqlite3_prepare_v2(h, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }  // no access: unknown
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        sqlite3_bind_text(stmt, 1, service, -1, transient)
        sqlite3_bind_text(stmt, 2, client, -1, transient)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        let allowed = sqlite3_column_int(stmt, 0) == allowedValue
        let size = Int(sqlite3_column_bytes(stmt, 1))
        guard size > 0, let blob = sqlite3_column_blob(stmt, 1) else { return .mine(allowed: allowed) }  // nothing to compare
        return satisfies(Data(bytes: blob, count: size)) ? .mine(allowed: allowed) : .older(allowed: allowed)
    }

    /// Does this running Maxie meet the code requirement macOS stored with a permission?
    static func selfSatisfies(_ csreq: Data) -> Bool {
        var req: SecRequirement?
        guard SecRequirementCreateWithData(csreq as CFData, [], &req) == errSecSuccess, let req else { return false }
        var code: SecCode?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code else { return false }
        return SecCodeCheckValidity(code, [], req) == errSecSuccess
    }
}
