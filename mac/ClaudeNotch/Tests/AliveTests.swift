import SwiftUI
import XCTest
@testable import ClaudeNotch

/// The panel tucks away when the user switches apps, Maxie steps aside from what they are working on,
/// motion is a spring (or nothing, with Reduce Motion), and the first open says hello. Pure rules and
/// off-screen renders only: nothing here moves a real window on the user's screen.
@MainActor
final class AliveTests: XCTestCase {
    private let screen = CGRect(x: 0, y: 0, width: 1512, height: 982)
    private let home = CGRect(x: 1300, y: 500, width: 54, height: 63)  // a free Maxie, right side

    // MARK: - Maxie steps aside

    func testNothingOverHimHeStays() {
        let w = CGRect(x: 100, y: 100, width: 800, height: 600)
        XCTAssertEqual(StepAside.move(bot: home, windows: [w], fullscreen: false, atNotch: false, screen: screen), .stay)
    }

    func testAFullscreenAppSendsHimUpAndOut() {
        XCTAssertEqual(StepAside.move(bot: home, windows: [], fullscreen: true, atNotch: true, screen: screen), .hide)
    }

    func testAWindowOverTheNotchHidesHimThereIsNowhereToGo() {
        let notch = CGRect(x: 738, y: 945, width: 36, height: 37)
        let w = CGRect(x: 0, y: 0, width: 1512, height: 982)
        XCTAssertEqual(StepAside.move(bot: notch, windows: [w], fullscreen: false, atNotch: true, screen: screen), .hide)
    }

    func testABarelyTouchingWindowIsNotCovering() {
        let w = CGRect(x: 0, y: 0, width: 1305, height: 982)  // 5 pt of his 54
        XCTAssertEqual(StepAside.move(bot: home, windows: [w], fullscreen: false, atNotch: false, screen: screen), .stay)
    }

    func testHeStepsToTheNearestClearSpot() throws {
        let w = CGRect(x: 1100, y: 200, width: 300, height: 500)  // the user's window, right under Maxie
        guard case .aside(let f) = StepAside.move(bot: home, windows: [w], fullscreen: false, atNotch: false, screen: screen) else {
            return XCTFail("he should step aside")
        }
        XCTAssertFalse(f.intersects(w), "off what the user is working on")
        XCTAssertTrue(screen.contains(f), "fully on screen")
        XCTAssertEqual(f.size, home.size)
        XCTAssertEqual(f.minX, w.maxX + StepAside.gap, accuracy: 0.5, "right of the window is closest")
    }

    func testWithNoRoomHePeeksFromTheEdge() throws {
        let w = CGRect(x: 0, y: 0, width: 1512, height: 940)  // a window filling the screen below the menu bar
        guard case .aside(let f) = StepAside.move(bot: home, windows: [w], fullscreen: false, atNotch: false, screen: screen) else {
            return XCTFail("he should peek")
        }
        let visible = f.intersection(screen)
        XCTAssertGreaterThan(visible.width, 8, "still there, a little")
        XCTAssertLessThan(visible.width, home.width * 0.5, "mostly out of the way")
    }

    /// The user dropped Maxie over their editor on purpose; a few seconds later Maxie slid off to the screen edge,
    /// and back there after every drag ("stuck in a corner, can't drag him back to the middle"). Their drop wins.
    func testWhereHeDropsHimOverAWindowHeStays() {
        let editor = CGRect(x: 0, y: 0, width: 1512, height: 940)
        let middle = CGRect(x: 700, y: 400, width: 54, height: 63)
        XCTAssertEqual(StepAside.move(bot: middle, windows: [editor], fullscreen: false, atNotch: false, screen: screen,
                                      accepted: editor), .stay)
    }

    func testANewWindowOverTheUsersDropStillMovesHim() {
        let editor = CGRect(x: 0, y: 0, width: 1512, height: 940)
        let browser = CGRect(x: 500, y: 300, width: 500, height: 400)  // slid over him later
        let middle = CGRect(x: 700, y: 400, width: 54, height: 63)
        guard case .aside = StepAside.move(bot: middle, windows: [browser, editor], fullscreen: false, atNotch: false,
                                           screen: screen, accepted: editor) else {
            return XCTFail("a window that came later still moves him")
        }
    }

    func testAFullscreenAppStillHidesHimWhereverHeWasDropped() {
        let editor = CGRect(x: 0, y: 0, width: 1512, height: 940)
        XCTAssertEqual(StepAside.move(bot: home, windows: [editor], fullscreen: true, atNotch: false, screen: screen,
                                      accepted: editor), .hide)
    }

    func testWindowBoundsTurnIntoScreenCoordinates() {
        // CGWindowList: top-left origin. A 100x50 window 10 pt from the top-left of a 982 pt tall screen.
        let r = StepAside.cocoa(CGRect(x: 0, y: 10, width: 100, height: 50), primaryHeight: 982)
        XCTAssertEqual(r, CGRect(x: 0, y: 922, width: 100, height: 50))
    }

    // MARK: - The panel tucks away

    func testThePanelTucksWhenHeSwitchesToAnotherAppUnlessPinned() {
        XCTAssertTrue(PanelRules.tucks(activated: 501, own: 42, pinned: false))
        XCTAssertFalse(PanelRules.tucks(activated: 501, own: 42, pinned: true), "pinned stays")
        XCTAssertFalse(PanelRules.tucks(activated: 42, own: 42, pinned: false), "Maxie itself (Settings) is not another app")
        XCTAssertFalse(PanelRules.tucks(activated: nil, own: 42, pinned: false))
    }

    func testTuckingShrinksTowardMaxie() {
        let panel = CGRect(x: 546, y: 300, width: 420, height: 620)
        let bot = CGRect(x: 738, y: 945, width: 36, height: 37)
        let f = Motion.tuckFrame(panel: panel, toward: bot)
        XCTAssertLessThan(f.width, panel.width)
        XCTAssertGreaterThan(f.midY, panel.midY, "drawn up toward him")
        XCTAssertEqual(f.width / f.height, panel.width / panel.height, accuracy: 0.01, "same shape")
    }

    func testReduceMotionMeansNoTravelAndNoBounce() {
        XCTAssertEqual(Motion.duration(Motion.open, reduce: true), Motion.fade)
        XCTAssertEqual(Motion.duration(Motion.open, reduce: false), Motion.open)
        let panel = CGRect(x: 0, y: 0, width: 420, height: 620)
        XCTAssertEqual(Motion.tuckFrame(panel: panel, toward: .zero, reduce: true), panel, "fade in place")
    }

    // MARK: - The first open

    func testTheWelcomeSaysHelloAndGotItClosesIt() {
        var done = false
        let harness = ClickHarness(WelcomeTurn(animate: false) { done = true }.frame(width: 380).padding(20),
                                   size: CGSize(width: 420, height: 320))
        defer { harness.close() }
        harness.settle(0.2)
        XCTAssertNotNil(harness.center(target: "welcome.hello"), "he says hello")
        harness.click(target: "welcome.done")
        XCTAssertTrue(done)
    }
}
