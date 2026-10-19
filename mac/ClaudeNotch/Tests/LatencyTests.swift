import SwiftUI
import XCTest
@testable import ClaudeNotch

/// A click shows its change in under 50 ms, and nothing holds the main thread for 100 ms.
/// Real views, real windows (off-screen, invisible); the clock runs from the mouse-up to the change being laid
/// out and drawn.
@MainActor
final class LatencyTests: XCTestCase {
    static let target = 0.050
    static let hang = 0.100

    /// Seconds from a click on `id` until `changed()` is true and the window has drawn it.
    private func latency(_ h: ClickHarness, _ id: String, until changed: () -> Bool) -> Double {
        guard let p = h.center(target: id) else { XCTFail("no \(id)"); return .infinity }
        let start = CACurrentMediaTime()
        h.press(at: p)
        while !changed(), CACurrentMediaTime() - start < 1 {
            RunLoop.main.run(until: Date().addingTimeInterval(0.001))
        }
        h.window.displayIfNeeded()
        let took = CACurrentMediaTime() - start
        h.settle(0.1)  // a person's next click comes later than 5 ms
        return took
    }

    override func setUp() async throws {
        _ = MascotLibrary.shared.loop(.idle)  // the app has the notch loops from launch
        try await Task.sleep(for: .seconds(2))  // and decodes the rest in the background
    }

    private func report(_ name: String, _ times: [Double]) {
        let sorted = times.sorted()
        let median = sorted[sorted.count / 2], worst = sorted.last ?? 0
        print(String(format: "PERF %@: median %.1f ms, worst %.1f ms over %d clicks", name, median * 1000, worst * 1000, times.count))
        XCTAssertLessThan(median, Self.target, "\(name): click to visible change")
        XCTAssertLessThan(worst, Self.hang, "\(name): no hang")
    }

    func testSwitchingSettingsSectionsIsInstant() {
        let hub = HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil)
        let view = SettingsView(hub: hub, notch: NotchController(hub: hub), initial: .account)
        let h = ClickHarness(view, size: SettingsWindow.size, window: { SettingsWindow.makeWindow(content: $0) })
        defer { h.close() }
        let order: [SettingsSection] = [.general, .voice, .rules, .connections, .backups, .speed, .aboutMe, .models, .permissions, .account]
        let times = order.map { s in
            let t = latency(h, "settings.row.\(s.rawValue)") { ClickTargets.values["settings.section"] == s.rawValue }
            print(String(format: "PERF   %@: %.1f ms", s.rawValue, t * 1000))
            return t
        }
        report("Settings section switch", times)
    }

    func testAWordChoiceAnswersInstantly() throws {
        let hub = HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil)
        hub.maxie = try JSONDecoder().decode(MaxieInfo.self, from: Data(ClickTests.maxieJSON.utf8))
        hub.maxieSink = { _ in }
        let h = ClickHarness(MaxieSection(hub: hub, notch: NotchController(hub: hub)).frame(width: 500).padding(20),
                             size: CGSize(width: 540, height: 1700))
        defer { h.close() }
        h.settle(0.5)
        var times: [Double] = []
        for s in [BotState.happy, .sleep, .think, .wave, .idle, .proud] {
            times.append(latency(h, "action.\(s.rawValue)") { ClickTargets.values["maxie.preview"] == s.rawValue })
        }
        report("Maxie action chip", times)
    }
}
