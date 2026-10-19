import XCTest
@testable import ClaudeNotch

/// The notch app reports lock, displays, input idle and media to the hub, and nothing else.
@MainActor
final class PresenceReporterTests: XCTestCase {
    func testTheBodyHasExactlyTheFourSignalsTheHubAccepts() {
        let s = PresenceReporter.Signals(locked: true, displaysAsleep: false, idleS: 1900.4, media: false)
        let body = s.body
        XCTAssertEqual(Set(body.keys), ["locked", "displays_asleep", "idle_s", "media"])
        XCTAssertEqual(body["locked"] as? Bool, true)
        XCTAssertEqual(body["displays_asleep"] as? Bool, false)
        XCTAssertEqual(body["idle_s"] as? Double, 1900)
        XCTAssertEqual(body["media"] as? Bool, false)
    }

    func testReadingTheMacsSignalsWorksWithoutAnyPermission() {
        let s = PresenceReporter.read(displaysAsleep: false)
        XCTAssertGreaterThanOrEqual(s.idleS, 0)  // the displays may be asleep: tests also run while the user is away
        XCTAssertTrue(JSONSerialization.isValidJSONObject(s.body))
    }

    func testDisplaysAsleepFromTheNotificationIsKept() {
        XCTAssertTrue(PresenceReporter.read(displaysAsleep: true).displaysAsleep)
    }
}
