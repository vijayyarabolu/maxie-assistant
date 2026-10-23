import XCTest
@testable import ClaudeNotch

/// a real Stop. Quit stops the hub, the voice helper and Laya until Maxie opens again; ⌥⇧P, the menu and
/// a click pause and wake him; a service that keeps crashing is stopped for a while, said in words, and tried again.
@MainActor
final class ServicesTests: XCTestCase {
    /// Every launchctl call the code makes, answered from `printed` (label → `launchctl print` text).
    private final class FakeLaunchd: @unchecked Sendable {
        var calls: [[String]] = []
        var printed: [String: String] = [:]

        func run(_ args: [String]) -> (Int32, String) {
            calls.append(args)
            guard args.first == "print", let label = args.last?.split(separator: "/").last else { return (0, "") }
            return printed[String(label)].map { (0, $0) } ?? (113, "Could not find service")
        }
    }

    private var launchd = FakeLaunchd()
    private let voice = Services.all[0]

    override func setUp() {
        super.setUp()
        launchd = FakeLaunchd()
        let fake = launchd
        Services.launchctl = { fake.run($0) }
    }

    override func tearDown() {
        Services.launchctl = { _ in (0, "") }  // never his real services from a test
        super.tearDown()
    }

    /// A watch whose launchctl calls are recorded here, never run (they would outlive the test).
    private func quietWatch() -> CrashWatch {
        let watch = CrashWatch()
        watch.stopService = { [launchd] in launchd.calls.append(["bootout", $0]) }
        watch.startService = { [launchd] in launchd.calls.append(["bootstrap", $0]) }
        return watch
    }

    private static func printed(runs: Int, exit: String = "last exit code = (never exited)") -> String {
        "gui/501/dev.maxie.voice = {\n\tactive count = 1\n\tstate = running\n\n\tjob state = running\n\truns = \(runs)\n\tpid = 7\n\t\(exit)\n\tendpoints = {\n\t\tstate = active\n\t}\n}\n"
    }

    func testLaunchdsPrintoutTellsACrashFromItsOwnRestart() {
        XCTAssertEqual(Services.parse(Self.printed(runs: 1)), .init(runs: 1, crashed: false, exit: ""))
        XCTAssertEqual(Services.parse(Self.printed(runs: 3, exit: "last exit code = 3")), .init(runs: 3, crashed: true, exit: "exit 3"))
        XCTAssertEqual(Services.parse(Self.printed(runs: 2, exit: "last terminating signal = Terminated: 15")),
                       .init(runs: 2, crashed: false, exit: ""), "kickstart -k: launchd's own SIGTERM is no crash")
        XCTAssertEqual(Services.parse(Self.printed(runs: 4, exit: "last terminating signal = Segmentation fault: 11")),
                       .init(runs: 4, crashed: true, exit: "signal 11"))
        XCTAssertNil(Services.parse("Bad request.\nCould not find service \"x\" in domain for user gui: 501\n"))
    }

    func testQuitStopsTheHubVoiceAndLayaSoNothingKeepsListening() {
        // Act
        Services.stopAll()

        // Assert
        let stopped = launchd.calls.filter { $0.first == "bootout" }.compactMap(\.last)
        XCTAssertEqual(Set(stopped), Set(Services.all.map { "\(Services.domain)/\($0.label)" }))
        XCTAssertEqual(launchd.calls.filter { $0.first == "disable" }.count, 3, "and launchd doesn't bring them back at login")
        XCTAssertEqual(launchd.calls.first?.last, "\(Services.domain)/\(MacHelpers.voiceLabel)", "the ears go first")
    }

    func testOpeningMaxieStartsOnlyTheServicesThatAreNotRunning() {
        // Arrange: the hub runs, voice and Laya were stopped by Quit
        launchd.printed[MacHelpers.hubLabel] = Self.printed(runs: 1)

        // Act
        Services.startMissing(exists: { _ in true })

        // Assert
        let started = launchd.calls.filter { $0.first == "bootstrap" }.compactMap(\.last)
        XCTAssertEqual(started, [Services.plist(MacHelpers.voiceLabel).path, Services.plist(Services.laya).path])
        XCTAssertEqual(launchd.calls.filter { $0.first == "enable" }.count, 2)
    }

    func testAServiceWithoutItsPlistIsNotStarted() {
        Services.startMissing(exists: { _ in false })
        XCTAssertTrue(launchd.calls.allSatisfy { $0.first == "print" })
    }

    func testBackoffDoublesUpToItsCapAndStartsOver() {
        var b = Backoff(first: 60, cap: 300)
        var waits: [TimeInterval] = []
        for _ in 0..<5 { b = b.next(); waits.append(b.wait) }
        XCTAssertEqual(waits, [60, 120, 240, 300, 300])
        XCTAssertEqual(b.reset().next().wait, 60)
    }

    func testThreeCrashesInTenMinutesStopTheServiceAndSaySo() {
        // Arrange
        let watch = quietWatch()
        var notices: [String] = []
        watch.onNotice = { notices.append($0) }
        let t0 = Date(timeIntervalSince1970: 1_000_000)

        // Act: seen running, then launchd restarted it after crashes
        watch.see(voice, .init(runs: 1, crashed: false, exit: ""), now: t0)
        watch.see(voice, .init(runs: 2, crashed: true, exit: "exit 1"), now: t0 + 60)
        XCTAssertFalse(watch.isStopped(voice.label), "one crash: launchd's restart is fine")
        watch.see(voice, .init(runs: 4, crashed: true, exit: "exit 1"), now: t0 + 120)

        // Assert
        XCTAssertTrue(watch.isStopped(voice.label))
        XCTAssertEqual(launchd.calls, [["bootout", voice.label]])
        XCTAssertEqual(notices, ["The voice helper kept crashing (exit 1), so I stopped it. Trying again in 10 min. Logs: Settings › General."])
        XCTAssertNotNil(watch.line, "the menu says it too, while it is stopped")
    }

    func testRestartsLaunchdDidForAKickstartAreNotCrashes() {
        let watch = quietWatch()
        let t0 = Date(timeIntervalSince1970: 1_000_000)
        watch.see(voice, .init(runs: 1, crashed: false, exit: ""), now: t0)
        watch.see(voice, .init(runs: 5, crashed: false, exit: ""), now: t0 + 60)  // Settings › Restart voice, four times
        XCTAssertFalse(watch.isStopped(voice.label))
    }

    func testAStoppedServiceIsTriedAgainLaterAndWaitsLongerIfItCrashesAgain() {
        // Arrange: stopped once
        let watch = quietWatch()
        var notices: [String] = []
        watch.onNotice = { notices.append($0) }
        let t0 = Date(timeIntervalSince1970: 1_000_000)
        watch.see(voice, .init(runs: 1, crashed: false, exit: ""), now: t0)
        watch.see(voice, .init(runs: 4, crashed: true, exit: "exit 1"), now: t0 + 60)

        // Act: not before its wait; after it, started again; it crashes again at once
        watch.see(voice, nil, now: t0 + 300)
        XCTAssertTrue(watch.isStopped(voice.label), "still waiting")
        watch.see(voice, nil, now: t0 + 60 + CrashWatch.window)
        XCTAssertFalse(watch.isStopped(voice.label), "tried again")
        XCTAssertEqual(launchd.calls.last, ["bootstrap", voice.label])
        let back = t0 + 60 + CrashWatch.window
        watch.see(voice, .init(runs: 1, crashed: false, exit: ""), now: back + 60)
        watch.see(voice, .init(runs: 4, crashed: true, exit: "exit 1"), now: back + 120)

        // Assert
        XCTAssertTrue(watch.isStopped(voice.label))
        XCTAssertEqual(notices.count, 2)
        XCTAssertTrue(notices[1].contains("Trying again in 20 min"), notices[1])
    }

    func testPausedIsFollowedFromTheHubsEvents() throws {
        // Arrange
        let h = HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil)

        // Act
        h.apply(try JSONDecoder().decode(HubEvent.self, from: Data(#"{"type":"paused","on":true}"#.utf8)))
        let pausedNow = h.paused
        h.apply(try JSONDecoder().decode(HubEvent.self, from: Data(#"{"type":"paused","on":false}"#.utf8)))

        // Assert
        XCTAssertTrue(pausedNow)
        XCTAssertFalse(h.paused)
    }

    func testPauseHasItsOwnShortcutAndACrashLoopHasAnIcon() {
        XCTAssertEqual(HotKeys.optionShiftP.label, "⌥⇧P")
        XCTAssertNotNil(VoiceNotice.icons["crash_loop"])
    }
}
