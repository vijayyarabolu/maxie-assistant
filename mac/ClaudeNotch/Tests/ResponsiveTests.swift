import AppKit
import XCTest
@testable import ClaudeNotch

/// "You can't open the application Maxie because it is not responding": debug builds now log
/// every main-thread stall with its stack, and a Dock click or `open` on a running Maxie brings up the panel.
@MainActor
final class ResponsiveTests: XCTestCase {
    func testAStallOnTheMainThreadIsLoggedWithItsStack() throws {
        let got = expectation(description: "a stall")
        let seen = Seen()
        let dog = MainThreadWatchdog(threshold: 0.25) { stall in
            seen.set(stall)
            got.fulfill()
        }
        dog.start()
        defer { dog.stop() }
        Thread.sleep(forTimeInterval: 0.7)  // the main thread, stuck
        wait(for: [got], timeout: 5)
        let stall = try XCTUnwrap(seen.stall)
        XCTAssertGreaterThanOrEqual(stall.ms, 250)
        XCTAssertTrue(stall.frames.contains { $0.localizedCaseInsensitiveContains("sleep") },
                      "the stack shows what the main thread was doing:\n\(stall.frames.joined(separator: "\n"))")
        XCTAssertTrue(stall.text.contains("main thread stalled"))
    }

    func testAnIdleMainThreadLogsNothing() {
        let got = expectation(description: "no stall")
        got.isInverted = true
        let dog = MainThreadWatchdog(threshold: 0.25) { _ in got.fulfill() }
        dog.start()
        defer { dog.stop() }
        wait(for: [got], timeout: 1.2)  // the run loop keeps turning
    }

    func testOpeningMaxieAgainBringsUpThePanel() {
        XCTAssertTrue(AppDelegate.instancesRespond(to: #selector(NSApplicationDelegate.applicationShouldHandleReopen(_:hasVisibleWindows:))),
                      "a Dock click or `open` on a running Maxie did nothing")
    }
}

private final class Seen: @unchecked Sendable {
    private let lock = NSLock()
    private var value: MainThreadWatchdog.Stall?

    func set(_ s: MainThreadWatchdog.Stall) { lock.withLock { if value == nil { value = s } } }
    var stall: MainThreadWatchdog.Stall? { lock.withLock { value } }
}

/// Opening Maxie took 4-5 clicks. A launch showed only the small Maxie at the notch (a menu-bar app has
/// no window), so it looked like nothing happened; and a second copy could start while the first was still there.
final class LaunchTests: XCTestCase {
    func testALaunchHeMadeOpensThePanel() {
        XCTAssertTrue(LaunchRules.opensPanel(isDefaultLaunch: true, asLoginItem: false), "Dock, Finder, Spotlight, open")
        XCTAssertFalse(LaunchRules.opensPanel(isDefaultLaunch: true, asLoginItem: true), "at login nobody is looking")
        XCTAssertFalse(LaunchRules.opensPanel(isDefaultLaunch: false, asLoginItem: false), "opened to handle a file or URL")
    }

    func testASecondCopyHandsOverToTheRunningOne() {
        XCTAssertTrue(LaunchRules.handsOff(others: [412]))
        XCTAssertFalse(LaunchRules.handsOff(others: []))
    }

    func testACopyStillQuittingDoesNotSwallowTheLaunch() {
        var looks = 0
        let others: () -> [pid_t] = { looks += 1; return looks < 3 ? [412] : [] }  // gone on the third look
        XCTAssertFalse(LaunchRules.waitForOthers(others, tries: 10, pause: 0), "it quit (macOS's Quit & Reopen): launch")
        XCTAssertEqual(looks, 3)
        XCTAssertTrue(LaunchRules.waitForOthers({ [412] }, tries: 3, pause: 0), "it stays: hand over")
    }

    func testLaunchTimeIsMeasuredFromTheProcessStart() throws {
        let started = try XCTUnwrap(LaunchRules.processStart())
        XCTAssertLessThan(started, Date())
        XCTAssertGreaterThan(started, Date().addingTimeInterval(-3600), "this test host started minutes ago")
    }
}
