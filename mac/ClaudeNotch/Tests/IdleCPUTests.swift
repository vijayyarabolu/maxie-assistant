import AppKit
import XCTest
@testable import ClaudeNotch

/// The app-side cost of an idle notch Maxie, measured as this process's CPU time while one bot sits
/// in an (off-screen, invisible) window for 10 s with the run loop free. Target: idle bot < 2% for the whole app.
@MainActor
final class IdleCPUTests: XCTestCase {
    private func cpuSeconds() -> Double {
        var u = rusage()
        getrusage(RUSAGE_SELF, &u)
        return Double(u.ru_utime.tv_sec + u.ru_stime.tv_sec) + Double(u.ru_utime.tv_usec + u.ru_stime.tv_usec) / 1e6
    }

    private func idleCost(seconds: Double, bot: Bool) -> Double {
        let w = NSWindow(contentRect: NSRect(x: -40_000, y: -40_000, width: 120, height: 120), styleMask: [.borderless],
                         backing: .buffered, defer: false)
        w.alphaValue = 0
        w.isReleasedWhenClosed = false
        if bot { w.contentView?.addSubview(MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))) }
        w.orderBack(nil)
        RunLoop.main.run(until: Date().addingTimeInterval(1))  // settle
        let start = cpuSeconds()
        RunLoop.main.run(until: Date().addingTimeInterval(seconds))
        let used = cpuSeconds() - start
        w.close()
        return used / seconds * 100
    }

    func testAnIdleBotBarelyWakesTheApp() {
        // Alternate and keep the quietest of each: in a full run, work left by earlier tests (web views, windows)
        // lands in whichever 10 s it happens to, and once read as a 4 % "bot" cost.
        var empties: [Double] = [], bots: [Double] = []
        for _ in 0..<3 {
            empties.append(idleCost(seconds: 4, bot: false))
            bots.append(idleCost(seconds: 4, bot: true))
        }
        let empty = empties.min() ?? 0
        let withBot = bots.min() ?? 0
        let bot = max(0, withBot - empty)
        print(String(format: "PERF idle bot (app side): %.2f%% of a core (empty window %.2f%%)", bot, empty))
        XCTAssertLessThan(bot, 0.5, "the loop plays in the render server, not on the main thread")
    }
}
