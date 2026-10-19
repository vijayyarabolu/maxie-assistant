import AppKit
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
        XCTAssertEqual(anim.repeatCount, .infinity)
        XCTAssertEqual(anim.calculationMode, .discrete)
        XCTAssertFalse(v.ticksEveryFrame, "no 30 fps timer on the main thread")
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
}
