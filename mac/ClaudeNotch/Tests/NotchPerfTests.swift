import AppKit
import WebKit
import XCTest
@testable import ClaudeNotch

/// The notch bot costs (almost) nothing while idle. Its loop and eye positions are Core Animation
/// keyframe animations played by the render server, not a 30 fps timer on the main thread; only blinks and
/// cursor glances wake the app, and only a little.
@MainActor
final class NotchPerfTests: XCTestCase {
    private func hosted(_ v: MascotLayerView) -> NSWindow {
        let w = NSWindow(contentRect: NSRect(x: -40_000, y: -40_000, width: 120, height: 120), styleMask: [.borderless],
                         backing: .buffered, defer: false)
        w.alphaValue = 0
        w.isReleasedWhenClosed = false
        w.contentView?.addSubview(v)
        return w
    }

    func testTheLoopIsAKeyframeAnimationNotATimer() throws {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        let w = hosted(v)
        defer { w.close() }
        let loop = try XCTUnwrap(MascotLibrary.shared.loop(.idle))
        let anim = try XCTUnwrap(v.loopAnimation, "the frames play as one animation")
        XCTAssertEqual(anim.values?.count, loop.meta.frames)
        XCTAssertEqual(anim.duration, Double(loop.meta.frames) / Double(loop.meta.fps), accuracy: 1e-9)
        XCTAssertEqual(anim.repeatCount, 1, "resting, the loop plays once, then he holds still")
        XCTAssertEqual(anim.calculationMode, .discrete)
        XCTAssertFalse(v.ticksEveryFrame, "no 30 fps timer on the main thread")
    }

    /// the idle loop played at 30 fps all day and WindowServer redrew the notch for it on every Space
    /// (WindowServer 16.6-20.1 % with Maxie on, 12.8-13.1 % off, screen locked). Resting, he breathes now and then.
    func testARestingMaxieBreathesNowAndThenInsteadOfLoopingForever() throws {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        let w = hosted(v)
        defer { w.close() }
        XCTAssertTrue(v.breathes, "idle: the loop comes back every \(MascotLayerView.breathEvery) s")
        v.state = .listen
        XCTAssertEqual(v.loopAnimation?.repeatCount, .infinity, "listening, thinking, talking: loops while it lasts")
        XCTAssertFalse(v.breathes)
        v.state = .sleep
        XCTAssertEqual(v.loopAnimation?.repeatCount, 1, "asleep all night: once, then still")
    }

    func testNothingPlaysWhileTheNotchIsNotOnScreen() throws {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        let w = hosted(v)
        defer { w.close() }
        v.state = .think
        v.setOnScreen(false)  // covered, a full-screen app, the screen locked
        XCTAssertNil(v.loopAnimation)
        XCTAssertTrue(v.eyeAnimations.isEmpty)
        XCTAssertFalse(v.breathes)
        XCTAssertNotNil(v.firstFrameShown)
        v.setOnScreen(true)
        XCTAssertEqual(v.loopAnimation?.repeatCount, .infinity, "back on screen: playing again")
    }

    func testTheEyesRideTheSameLoop() throws {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        let w = hosted(v)
        defer { w.close() }
        let loop = try XCTUnwrap(MascotLibrary.shared.loop(.idle))
        guard loop.liveEyes else { throw XCTSkip("these loops draw their own eyes") }
        let eyes = v.eyeAnimations
        XCTAssertEqual(eyes.count, 2)
        for a in eyes { XCTAssertEqual(a.values?.count, loop.meta.frames); XCTAssertEqual(a.duration, v.loopAnimation?.duration) }
    }

    func testReducedMotionHoldsTheFirstFrameStill() throws {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        v.reduceMotion = true
        let w = hosted(v)
        defer { w.close() }
        XCTAssertNil(v.loopAnimation)
        XCTAssertTrue(v.eyeAnimations.isEmpty)
        XCTAssertNotNil(v.firstFrameShown, "the model layer shows frame 0, so a still capture looks right")
    }

    func testAStateChangeSwapsTheLoop() throws {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        let w = hosted(v)
        defer { w.close() }
        let before = v.loopAnimation?.values?.first as AnyObject?
        v.state = .sleep
        let after = v.loopAnimation?.values?.first as AnyObject?
        XCTAssertNotNil(after)
        XCTAssertFalse(before === after, "the sleep loop's frames, not idle's")
    }

    /// The eyes land exactly where the old 30 fps code put them in every frame: anchor centre, anchor size.
    func testEveryFramesEyesMatchTheAnchors() throws {
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 36, height: 42))
        let w = hosted(v)
        defer { w.close() }
        let loop = try XCTUnwrap(MascotLibrary.shared.loop(.idle))
        guard loop.liveEyes, let anchors = loop.meta.eye_anchors_2x else { throw XCTSkip("no live eyes") }
        let base = anchors[0]
        for (n, anim) in v.eyeAnimations.enumerated() {
            let a0 = n == 0 ? base.left : base.right
            for (i, value) in (anim.values ?? []).enumerated() {
                let t = (value as! NSValue).caTransform3DValue
                let a = n == 0 ? anchors[i].left : anchors[i].right
                XCTAssertEqual(a0.x / 2 + t.m41, a.x / 2, accuracy: 1e-6, "frame \(i) eye \(n) x")
                XCTAssertEqual(a0.y / 2 + t.m42, a.y / 2, accuracy: 1e-6, "frame \(i) eye \(n) y")
                XCTAssertEqual(a0.w / 2 * t.m11, a.w / 2, accuracy: 1e-6)
                XCTAssertEqual(max(0.6, a0.h / 2) * t.m22, max(0.6, a.h / 2), accuracy: 1e-6)
            }
        }
    }

    /// An earlier build had 61 copies of Maxie's window stacked at the notch: every rebuild (display change, screen
    /// wake, Home or Size) hid the old window but never closed it, so its views and timers lived on.
    func testADiscardedBotWindowTakesItsViewsWithIt() {
        let panel = NSPanel(contentRect: NSRect(x: -30_000, y: -30_000, width: 37, height: 42), styleMask: [.borderless], backing: .buffered, defer: false)
        panel.alphaValue = 0  // never on the user's screen
        let bot = MascotLayerView(frame: NSRect(x: 0, y: 0, width: 37, height: 42))
        panel.contentView = bot
        panel.orderBack(nil)
        XCTAssertNotNil(bot.window)
        NotchController.discard(panel)
        XCTAssertNil(bot.window, "Maxie left the window, so his blink and glance timers stopped")
        XCTAssertFalse(panel.isVisible)
    }

    func testTheOpenPanelsMaxieHoldsStillWhenIdleAndMovesWhenHeWorks() {
        XCTAssertTrue(LiveBotView.rests(.idle))
        XCTAssertTrue(LiveBotView.rests(.sleep))
        XCTAssertFalse(LiveBotView.rests(.listen))
        XCTAssertFalse(LiveBotView.rests(.talk))
        let web = BotWebView(frame: .zero, configuration: WKWebViewConfiguration())
        web.rest(true)
        XCTAssertFalse(web.resting, "the move into idle finishes first")
        RunLoop.main.run(until: Date().addingTimeInterval(BotWebView.settleS + 0.3))
        XCTAssertTrue(web.resting)
        web.rest(false)
        XCTAssertFalse(web.resting, "thinking: draws again at once")
    }
}
