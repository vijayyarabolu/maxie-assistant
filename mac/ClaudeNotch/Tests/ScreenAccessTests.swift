import Security
import SQLite3
import XCTest
@testable import ClaudeNotch

/// Screen Recording's "Turn on" did nothing after the new signature: macOS shows its prompt once per
/// app identity, then never again. Now the row says what to do next and opens the right System Settings page.
final class ScreenAccessTests: XCTestCase {
    private var db: URL!

    override func setUpWithError() throws {
        db = FileManager.default.temporaryDirectory.appending(path: "tcc-\(UUID().uuidString).db")
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: db)
    }

    func testWhatTheRowOffers() {
        typealias A = ScreenAccess
        XCTAssertEqual(A.advice(granted: true, sentToSettings: true, entry: .older(allowed: true)), .allowed)
        XCTAssertEqual(A.advice(granted: false, sentToSettings: false, entry: nil), .turnOn, "macOS may still ask")
        XCTAssertEqual(A.advice(granted: false, sentToSettings: true, entry: nil), .turnOnInSettings)
        XCTAssertEqual(A.advice(granted: false, sentToSettings: false, entry: .mine(allowed: false)), .turnOnInSettings,
                       "listed but off: only the switch helps")
        XCTAssertEqual(A.advice(granted: false, sentToSettings: true, entry: .mine(allowed: true)), .reopen,
                       "on for this build: macOS applies it after a reopen")
        XCTAssertEqual(A.advice(granted: false, sentToSettings: false, entry: .older(allowed: true)), .removeOld,
                       "the switch belongs to an older Maxie")
    }

    func testTheLineSaysWhereAndWhatNext() {
        XCTAssertEqual(ScreenAccess.line, "Turn on Maxie here, then reopen Maxie.")
        XCTAssertEqual(Permission.screen.pane, "com.apple.preference.security?Privacy_ScreenCapture")
    }

    func testReadsMaxiesRowInTheTCCDatabase() throws {
        try makeDB(rows: [("kTCCServiceScreenCapture", "dev.maxie.notch", 2),
                          ("kTCCServiceMicrophone", "dev.maxie.notch", 0),
                          ("kTCCServiceScreenCapture", "com.other.app", 0)])
        XCTAssertEqual(ScreenAccess.entry(db: db, client: "dev.maxie.notch", satisfies: { _ in true }), .mine(allowed: true))
        XCTAssertEqual(ScreenAccess.entry(db: db, client: "dev.maxie.notch", satisfies: { _ in false }), .older(allowed: true))
        XCTAssertNil(ScreenAccess.entry(db: db, client: "com.nobody", satisfies: { _ in true }), "not listed")
    }

    func testAnOffSwitchIsStillAnEntry() throws {
        try makeDB(rows: [("kTCCServiceScreenCapture", "dev.maxie.notch", 0)])
        XCTAssertEqual(ScreenAccess.entry(db: db, client: "dev.maxie.notch", satisfies: { _ in true }), .mine(allowed: false))
    }

    func testAnUnreadableDatabaseIsUnknownNotAnError() {
        XCTAssertNil(ScreenAccess.entry(db: URL(fileURLWithPath: "/nonexistent/TCC.db"), client: "x", satisfies: { _ in true }))
    }

    func testThisBuildSatisfiesItsOwnRequirementAndNotAnothers() throws {
        var me: SecCode?
        XCTAssertEqual(SecCodeCopySelf([], &me), errSecSuccess)
        var stat: SecStaticCode?
        XCTAssertEqual(SecCodeCopyStaticCode(try XCTUnwrap(me), [], &stat), errSecSuccess)
        var mine: SecRequirement?
        XCTAssertEqual(SecCodeCopyDesignatedRequirement(try XCTUnwrap(stat), [], &mine), errSecSuccess)
        XCTAssertTrue(ScreenAccess.selfSatisfies(try data(try XCTUnwrap(mine))))
        var other: SecRequirement?
        XCTAssertEqual(SecRequirementCreateWithString("identifier \"com.someone.else\"" as CFString, [], &other), errSecSuccess)
        XCTAssertFalse(ScreenAccess.selfSatisfies(try data(try XCTUnwrap(other))))
        XCTAssertFalse(ScreenAccess.selfSatisfies(Data([1, 2, 3])), "garbage is not a match")
    }

    func testReopenStartsThisMaxieAgainAfterItQuits() {
        let app = URL(fileURLWithPath: "/Applications/Maxie \"x\".app")
        XCTAssertEqual(MacHelpers.reopenArguments(bundle: app), ["-c", "sleep 1; /usr/bin/open \"$0\"", app.path],
                       "the path is an argument, never pasted into the script")
    }

    // MARK: helpers

    private func data(_ r: SecRequirement) throws -> Data {
        var out: CFData?
        XCTAssertEqual(SecRequirementCopyData(r, [], &out), errSecSuccess)
        return try XCTUnwrap(out) as Data
    }

    private func makeDB(rows: [(String, String, Int)]) throws {
        var h: OpaquePointer?
        XCTAssertEqual(sqlite3_open(db.path, &h), SQLITE_OK)
        defer { sqlite3_close(h) }
        XCTAssertEqual(sqlite3_exec(h, "CREATE TABLE access (service TEXT, client TEXT, client_type INTEGER, auth_value INTEGER, csreq BLOB)", nil, nil, nil), SQLITE_OK)
        for (service, client, value) in rows {
            let sql = "INSERT INTO access VALUES ('\(service)', '\(client)', 0, \(value), X'FADE0C00')"
            XCTAssertEqual(sqlite3_exec(h, sql, nil, nil, nil), SQLITE_OK)
        }
    }
}
