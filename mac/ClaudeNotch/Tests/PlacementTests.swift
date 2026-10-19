import XCTest
@testable import ClaudeNotch

final class PlacementTests: XCTestCase {
    let screen = CGRect(x: 0, y: 0, width: 1512, height: 982)
    let size = CGSize(width: 45, height: 75)
    var home: CGRect { CGRect(x: 854, y: 931, width: 45, height: 75) }

    func testClampKeepsTheWholeBotOnScreen() {
        XCTAssertEqual(BotGeometry.clamp(CGPoint(x: -40, y: -10), size: size, in: screen), CGPoint(x: 0, y: 0))
        XCTAssertEqual(BotGeometry.clamp(CGPoint(x: 2000, y: 2000), size: size, in: screen), CGPoint(x: 1512 - 45, y: 982 - 75))
        XCTAssertEqual(BotGeometry.clamp(CGPoint(x: 300, y: 300), size: size, in: screen), CGPoint(x: 300, y: 300))
    }

    func testSnapNearHomeGoesBackToTheNotch() {
        let p = BotGeometry.snap(drop: CGPoint(x: 870, y: 900), size: size, in: screen, home: home, display: 1)
        XCTAssertEqual(p, .notch(on: 1))
    }

    func testSnapNearEdgesTucksAndCornersCountAsEdges() {
        XCTAssertEqual(BotGeometry.snap(drop: CGPoint(x: 10, y: 400), size: size, in: screen, home: home, display: 1).mode, .tuckedLeft)
        XCTAssertEqual(BotGeometry.snap(drop: CGPoint(x: 1480, y: 400), size: size, in: screen, home: home, display: 1).mode, .tuckedRight)
        let corner = BotGeometry.snap(drop: CGPoint(x: 1600, y: -50), size: size, in: screen, home: home, display: 1)
        XCTAssertEqual(corner.mode, .tuckedRight)
        XCTAssertEqual(corner.y, 0)
    }

    func testFreeSpotIsKeptAndClamped() {
        let p = BotGeometry.snap(drop: CGPoint(x: 600, y: 2000), size: size, in: screen, home: home, display: 1)
        XCTAssertEqual(p.mode, .free)
        XCTAssertEqual(p.y, 982 - 75)
        let f = BotGeometry.frame(for: p, size: size, in: screen, home: home)
        XCTAssertTrue(screen.contains(f))
    }

    func testTuckedFramePeeksAndSlidesOutOnHover() {
        let p = BotPlacement(mode: .tuckedRight, x: 0, y: 300, displayID: 1)
        let tucked = BotGeometry.frame(for: p, size: size, in: screen, home: home)
        XCTAssertEqual(tucked.minX, 1512 - 45 * BotGeometry.peek, accuracy: 0.01)
        let out = BotGeometry.frame(for: p, size: size, in: screen, home: home, peeking: true)
        XCTAssertEqual(out.maxX, 1512, accuracy: 0.01)
        let left = BotGeometry.frame(for: BotPlacement(mode: .tuckedLeft, x: 0, y: 300, displayID: 1), size: size, in: screen, home: home)
        XCTAssertEqual(left.maxX, 45 * BotGeometry.peek, accuracy: 0.01)
    }

    func testPanelNeverOffScreen() {
        let panel = CGSize(width: 420, height: 620)
        let nearBottomLeft = CGRect(x: 0, y: 10, width: 45, height: 75)
        let o = BotGeometry.panelOrigin(bot: nearBottomLeft, panel: panel, screen: screen, atNotch: false)
        XCTAssertTrue(screen.contains(CGRect(origin: o, size: panel)))
        XCTAssertEqual(o.y, nearBottomLeft.maxY + BotGeometry.panelGap)  // above Maxie: no room below
        let atNotch = BotGeometry.panelOrigin(bot: home, panel: panel, screen: screen, atNotch: true)
        XCTAssertTrue(screen.contains(CGRect(origin: atNotch, size: panel)))
        XCTAssertEqual(atNotch.x, home.midX - panel.width / 2, accuracy: 0.01)
    }

    func testStoreSavesPerDisplayAndRestoresOrFallsBackToMain() {
        let defaults = UserDefaults(suiteName: "PlacementTests-\(UUID().uuidString)")!
        let store = PlacementStore(defaults: defaults)
        XCTAssertEqual(store.restore(connected: [1], main: 1), .notch(on: 1))
        let external = BotPlacement(mode: .free, x: 100, y: 200, displayID: 2)
        store.save(external)
        store.save(BotPlacement(mode: .tuckedLeft, x: 0, y: 500, displayID: 1))
        XCTAssertEqual(store.restore(connected: [2, 1], main: 1), external)
        XCTAssertEqual(store.restore(connected: [1], main: 1).mode, .tuckedLeft)   // the external display is unplugged
        XCTAssertEqual(store.restore(connected: [3], main: 3), .notch(on: 3))      // nothing saved for this one
        store.reset()
        XCTAssertEqual(store.restore(connected: [1], main: 1), .notch(on: 1))
    }
}
