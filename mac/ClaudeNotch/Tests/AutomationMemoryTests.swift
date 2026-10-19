import XCTest
@testable import ClaudeNotch

/// macOS can only answer the Automation question for a running app, so a quit Music showed
/// "Turn on" although the grant was still stored. Maxie remembers the last answer per app, with the date.
final class AutomationMemoryTests: XCTestCase {
    private var defaults: UserDefaults!
    private let music = "com.apple.Music"
    private let then = Date(timeIntervalSince1970: 1_800_000_000)

    override func setUp() {
        defaults = UserDefaults(suiteName: "automation-\(UUID().uuidString)")
    }

    func testGrantMusicQuitMusicReopenSettingsStillSaysAllowed() {
        _ = AutomationMemory(defaults: defaults).resolve(.allowed, for: music, now: then)
        let reopened = AutomationMemory(defaults: defaults)  // a new Settings window, same Mac
        XCTAssertEqual(reopened.resolve(.notRunning, for: music, now: then.addingTimeInterval(3600)),
                       AutomationStatus(grant: .allowed, checked: then))
    }

    func testARunningAppAlwaysShowsWhatMacOSSaysNow() {
        let memory = AutomationMemory(defaults: defaults)
        XCTAssertEqual(memory.resolve(.allowed, for: music, now: then), AutomationStatus(grant: .allowed, checked: nil))
        XCTAssertEqual(memory.resolve(.denied, for: music, now: then), AutomationStatus(grant: .denied, checked: nil))
        XCTAssertEqual(memory.resolve(.notRunning, for: music, now: then), AutomationStatus(grant: .denied, checked: then),
                       "turned off in System Settings: remembered as off, not as allowed")
    }

    func testNotAskedForgetsTheOldAnswer() {
        let memory = AutomationMemory(defaults: defaults)
        _ = memory.resolve(.allowed, for: music, now: then)
        XCTAssertEqual(memory.resolve(.notAsked, for: music, now: then).grant, .notAsked)  // reset with tccutil
        XCTAssertEqual(memory.resolve(.notRunning, for: music, now: then).grant, .notAsked)
    }

    func testAQuitAppNeverAskedAboutShowsTurnOn() {
        XCTAssertEqual(AutomationMemory(defaults: defaults).resolve(.notRunning, for: music, now: then),
                       AutomationStatus(grant: .notAsked, checked: nil))
    }

    func testEachAppIsRememberedOnItsOwn() {
        let memory = AutomationMemory(defaults: defaults)
        _ = memory.resolve(.allowed, for: music, now: then)
        XCTAssertEqual(memory.resolve(.notRunning, for: "com.apple.Notes", now: then).grant, .notAsked)
    }

    func testTheRowSaysWhenItWasLastChecked() {
        let label = AutomationStatus(grant: .allowed, checked: then).label(now: then.addingTimeInterval(2 * 3600))
        XCTAssertTrue(label.hasPrefix("Allowed · checked "), label)
        XCTAssertEqual(AutomationStatus(grant: .allowed, checked: nil).label(now: then), "Allowed")
    }

    func testATaskResultIsAnAnswerToo() {
        XCTAssertEqual(ScriptStore.grant(after: nil), .allowed)
        XCTAssertEqual(ScriptStore.grant(after: .denied("automation")), .denied)
        XCTAssertNil(ScriptStore.grant(after: .failed("timeout")), "a timeout says nothing about the grant")
    }

    func testQuitAppsOpenHiddenAndNeverComeToTheFront() {
        let config = HiddenLaunch.configuration()
        XCTAssertFalse(config.activates)
        XCTAssertTrue(config.hides)
    }

    func testEveryScriptedAppIsAnAutomationRow() {
        let rows = Set(AutomationTarget.all.map(\.bundleID))
        for id in ScriptStore.apps.values { XCTAssertTrue(rows.contains(id), id) }
    }
}
