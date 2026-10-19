import AppKit
import XCTest
@testable import ClaudeNotch

/// The notch Maxie only seemed to sleep or blink while the user worked. His eyes moved 1 pt at
/// most (a third of an eye: you can't see it at the notch) and a paused mic closed them. Now he watches where the user
/// is working, the pointer or the window they type in, leans toward it, and strolls while Maxie works himself.
@MainActor
final class WatchingTests: XCTestCase {
    private let screen = CGRect(x: 0, y: 0, width: 1512, height: 982)
    private let home = CGRect(x: 700, y: 400, width: 54, height: 63)  // a free Maxie, mid-screen

    // MARK: - Where the user is working

    func testWhileHeTypesMaxieLooksAtTheWindowHeTypesIn() {
        let editor = CGRect(x: 100, y: 100, width: 800, height: 600)
        let mouse = CGPoint(x: 1400, y: 900)
        XCTAssertEqual(Alive.attention(mouse: mouse, typingIn: editor, sinceKey: 0.3, sinceMouse: 8), CGPoint(x: 500, y: 400))
        XCTAssertEqual(Alive.attention(mouse: mouse, typingIn: editor, sinceKey: 8, sinceMouse: 0.3), mouse, "the mouse moved last")
        XCTAssertEqual(Alive.attention(mouse: mouse, typingIn: nil, sinceKey: 0.3, sinceMouse: 8), mouse, "no window: the pointer")
    }

    func testAtTheNotchHisEyesMoveEnoughToSee() {
        let origin = CGPoint(x: 800, y: 960)
        let right = CursorLook.offset(to: CGPoint(x: 1400, y: 960), from: origin, max: CursorLook.reach)
        XCTAssertGreaterThanOrEqual(right.width, 1.7, "1 pt read as just blinking")
        XCTAssertEqual(right.height, 0, accuracy: 0.01)
        let below = CursorLook.offset(to: CGPoint(x: 800, y: 300), from: origin, max: CursorLook.reach)
        XCTAssertGreaterThan(below.height, 1.7, "view y goes down: the pointer below, he looks down")
        let near = CursorLook.offset(to: CGPoint(x: 830, y: 960), from: origin, max: CursorLook.reach)
        XCTAssertLessThan(near.width, right.width, "close by, a smaller glance")
    }

    // MARK: - He leans toward it

    func testHeLeansTowardWhereHeIsWorking() {
        XCTAssertEqual(Alive.tilt(look: CGSize(width: 1, height: 0), reduce: false), Alive.maxTilt, "right of him: lean right")
        XCTAssertEqual(Alive.tilt(look: CGSize(width: -1, height: 0.5), reduce: false), -Alive.maxTilt)
        XCTAssertEqual(Alive.tilt(look: CGSize(width: 3, height: 0), reduce: false), Alive.maxTilt, "never more than a lean")
        XCTAssertEqual(Alive.tilt(look: CGSize(width: 1, height: 0), reduce: true), 0, "Reduce motion: upright")
    }

    func testTheNotchMaxieLeansWithHisEyes() {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        v.follow(CGSize(width: CursorLook.reach, height: 0))
        XCTAssertEqual(v.lean, Alive.maxTilt, accuracy: 0.01)
        v.follow(CGSize(width: -CursorLook.reach / 2, height: 0))
        XCTAssertEqual(v.lean, -Alive.maxTilt / 2, accuracy: 0.01)
        v.state = .think  // working: eyes on the task, upright
        XCTAssertEqual(v.lean, 0)
    }

    // MARK: - Eyes closed only when he isn't watching or the user sleeps

    func testPausingTheMicDoesNotCloseHisEyes() {
        XCTAssertEqual(MiniBot.face(connected: true, state: .idle), .idle, "a paused mic isn't \"stop watching\"")
        XCTAssertEqual(MiniBot.face(connected: true, state: .closed), .closed, "the user said stop watching (the hub's base)")
        XCTAssertEqual(MiniBot.face(connected: true, state: .sleep), .sleep, "the user is asleep (presence)")
        XCTAssertEqual(MiniBot.face(connected: false, state: .idle), .sleep, "hub offline")
    }

    func testBackOnlineMaxieTellsTheHubAtOnceThatHeIsAwake() {
        let hub = HubClient(base: URL(string: "http://127.0.0.1:9")!, token: "test")
        XCTAssertNil(hub.onConnect)
        PresenceReporter.shared.attach(hub)
        XCTAssertNotNil(hub.onConnect, "a restarted hub has no report yet and falls back to the clock: asleep at night")
    }

    // MARK: - He strolls while he works

    func testWithRoomBothWaysHeStrollsSideToSide() {
        let spots = Alive.walk(home: home, screen: screen, blocked: [], reduce: false)
        XCTAssertEqual(spots, [home.offsetBy(dx: -Alive.reach, dy: 0), home.offsetBy(dx: Alive.reach, dy: 0)])
    }

    func testHeNeverStrollsOverTheUsersWorkOrTheChat() {
        let chat = CGRect(x: home.maxX + 4, y: 200, width: 420, height: 620)  // open beside him, on the right
        let spots = Alive.walk(home: home, screen: screen, blocked: [chat], reduce: false)
        XCTAssertEqual(spots, [home, home.offsetBy(dx: -Alive.reach, dy: 0)], "home and the free side")
        let editor = CGRect(x: 0, y: 0, width: home.minX + 10, height: 982)
        XCTAssertEqual(Alive.walk(home: home, screen: screen, blocked: [chat, editor], reduce: false), [], "no room: he stays")
    }

    func testOverTheWindowHeDroppedHimOnHeMayStroll() {
        let editor = CGRect(x: 0, y: 0, width: 1512, height: 940)
        XCTAssertEqual(Alive.walk(home: home, screen: screen, blocked: [editor], accepted: editor, reduce: false).count, 2)
        XCTAssertEqual(Alive.walk(home: home, screen: screen, blocked: [editor], reduce: false), [])
    }

    func testHeNeverStrollsOffScreenAndReduceMotionKeepsHimStill() {
        let edge = CGRect(x: screen.maxX - home.width - 10, y: 400, width: home.width, height: home.height)
        XCTAssertEqual(Alive.walk(home: edge, screen: screen, blocked: [], reduce: false), [edge, edge.offsetBy(dx: -Alive.reach, dy: 0)])
        XCTAssertEqual(Alive.walk(home: home, screen: screen, blocked: [], reduce: true), [])
    }

    func testAStrollEndsWhenSomethingElseTakesOver() {
        let w = NSWindow(contentRect: CGRect(x: -40_000, y: -40_000, width: 54, height: 63), styleMask: .borderless,
                         backing: .buffered, defer: true)
        let stroll = Stroll()
        stroll.start(w, spots: [w.frame.offsetBy(dx: -36, dy: 0), w.frame.offsetBy(dx: 36, dy: 0)])
        XCTAssertTrue(stroll.isWalking)
        stroll.halt()  // a drag, stepping aside: one owner of where Maxie is
        XCTAssertFalse(stroll.isWalking)
        stroll.start(w, spots: [])
        XCTAssertFalse(stroll.isWalking, "no room: no stroll")
        w.close()
    }
}
