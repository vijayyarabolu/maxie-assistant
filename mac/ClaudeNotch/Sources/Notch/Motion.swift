import AppKit
import QuartzCore

/// How Maxie's windows move: a spring that settles with a touch of overshoot, never linear,
/// never instant. With Reduce Motion on (System Settings › Accessibility), windows only fade, in place.
enum Motion {
    static let open: TimeInterval = 0.34   // the panel opening out of Maxie
    static let tuck: TimeInterval = 0.22   // the panel folding back into him
    static let step: TimeInterval = 0.38   // Maxie stepping aside or coming back
    static let fade: TimeInterval = 0.12   // Reduce Motion: everything is a short fade
    static let scale: CGFloat = 0.92       // the panel at the start of opening / end of tucking
    static let pull: CGFloat = 0.08        // how far it is drawn toward Maxie

    /// Out fast, settle with a slight overshoot (a spring's shape in a curve AppKit window animation takes).
    static var spring: CAMediaTimingFunction { CAMediaTimingFunction(controlPoints: 0.32, 1.22, 0.42, 1) }
    /// Leaving: quick, no bounce.
    static var away: CAMediaTimingFunction { CAMediaTimingFunction(controlPoints: 0.4, 0, 1, 1) }

    static var reduce: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }

    static func duration(_ base: TimeInterval, reduce: Bool = Motion.reduce) -> TimeInterval {
        reduce ? fade : base
    }

    /// The panel, smaller and drawn toward Maxie: where opening starts and tucking ends.
    static func tuckFrame(panel: CGRect, toward bot: CGRect, reduce: Bool = Motion.reduce) -> CGRect {
        guard !reduce else { return panel }
        let size = CGSize(width: panel.width * scale, height: panel.height * scale)
        let mid = CGPoint(x: panel.midX + (bot.midX - panel.midX) * pull, y: panel.midY + (bot.midY - panel.midY) * pull)
        return CGRect(x: mid.x - size.width / 2, y: mid.y - size.height / 2, width: size.width, height: size.height)
    }

    /// Animate a window's frame and alpha; `done` runs when it has arrived.
    @MainActor
    static func animate(_ window: NSWindow, to frame: CGRect, alpha: CGFloat, duration: TimeInterval,
                        curve: CAMediaTimingFunction = spring, done: (@MainActor () -> Void)? = nil) {
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = duration
            ctx.timingFunction = curve
            ctx.allowsImplicitAnimation = true
            window.animator().setFrame(frame, display: true)
            window.animator().alphaValue = alpha
        } completionHandler: {
            MainActor.assumeIsolated { done?() }
        }
    }
}
