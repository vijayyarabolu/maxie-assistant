import XCTest
@testable import ClaudeNotch

/// The pointer near Maxie at the top made him jump and flicker. Hover now has a dead zone and a
/// short settle (HoverGate), and only one thing moves him at a time.
@MainActor
final class HoverTests: XCTestCase {
    private let bot = CGRect(x: 800, y: 919, width: 36, height: 42)  // at the notch
    private let step: TimeInterval = 0.02  // the pointer sampled every 20 ms

    /// The pointer goes back and forth across his left edge, `swing` points each side, crossing it `times` times.
    private func crossings(_ times: Int, swing: CGFloat, from t0: TimeInterval) -> [(CGPoint, TimeInterval)] {
        (0..<times).map { n in (CGPoint(x: bot.minX + (n.isMultiple(of: 2) ? swing : -swing), y: bot.midY), t0 + Double(n) * step * 2) }
    }

    private func run(_ samples: [(CGPoint, TimeInterval)], frame: (Bool) -> CGRect, gate: inout HoverGate) -> [Bool] {
        samples.compactMap { p, t in gate.update(p, frame: frame(gate.inside), now: t) }
    }

    func testThePointerCrossingHisEdge50TimesNeverFlickersHim() {
        var gate = HoverGate()
        XCTAssertEqual(run(crossings(50, swing: 3, from: 0), frame: { _ in self.bot }, gate: &gate), [], "a pointer brushing his edge")
        XCTAssertFalse(gate.inside)
    }

    func testOnHimHeOpensOnceAndJitterAtTheEdgeKeepsItOpen() {
        var gate = HoverGate()
        let rest = (0..<20).map { n in (CGPoint(x: bot.midX, y: bot.midY), Double(n) * step) }
        let jitter = crossings(50, swing: 3, from: 1)  // within the dead zone
        let away = (0..<20).map { n in (CGPoint(x: bot.minX - 40, y: bot.midY), 3 + Double(n) * step) }
        XCTAssertEqual(run(rest + jitter + away, frame: { _ in self.bot }, gate: &gate), [true, false], "in once, out once")
    }

    func testAQuickPassAcrossHimDoesNotOpenTheChat() {
        var gate = HoverGate()
        let pass = (0..<5).map { n in (CGPoint(x: bot.minX - 20 + CGFloat(n) * 20, y: bot.midY), Double(n) * step) }  // 80 ms
        XCTAssertEqual(run(pass, frame: { _ in self.bot }, gate: &gate), [])
    }

    func testATuckedMaxieSlidingOutUnderThePointerStaysOut() {
        let tucked = CGRect(x: -20, y: 400, width: 36, height: 42), peeking = CGRect(x: 0, y: 400, width: 36, height: 42)
        var gate = HoverGate()
        let near = (0..<200).map { n in (CGPoint(x: 6 + CGFloat(n % 2) * 4, y: 420), Double(n) * step) }  // at his edge, 4 s
        XCTAssertEqual(run(near, frame: { $0 ? peeking : tucked }, gate: &gate), [true], "out once, no slide back and forth")
    }

    func testOneThingMovesHimAtATime() {
        XCTAssertTrue(NotchController.mayStepAside(peeking: false, dragging: false, following: false))
        XCTAssertFalse(NotchController.mayStepAside(peeking: true, dragging: false, following: false), "the pointer is on him")
        XCTAssertFalse(NotchController.mayStepAside(peeking: false, dragging: true, following: false))
        XCTAssertFalse(NotchController.mayStepAside(peeking: false, dragging: false, following: true))
    }
}
