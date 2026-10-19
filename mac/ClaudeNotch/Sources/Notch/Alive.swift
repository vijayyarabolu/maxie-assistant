import AppKit

/// Maxie feels alive. He watches where the user is working, the pointer or the window they are
/// typing in: his eyes follow it and he leans toward it. While Maxie works (thinking) a free Maxie strolls a few
/// steps to one side and back. Small, never in the way: the stroll never covers the front app's windows or the
/// chat, never leaves the screen, and Reduce motion keeps him still and upright.
enum Alive {
    /// The most he leans, in degrees.
    static let maxTilt: Double = 5
    /// How far he walks each way, in points.
    static let reach: CGFloat = 36
    /// One way, in seconds: an easy stroll.
    static let pace: TimeInterval = 1.6
    /// A breath at each end before he turns back.
    static let pause: TimeInterval = 0.8

    /// Where the user is working: the pointer, or while they type (a key newer than the last mouse move) the middle of the
    /// window they are typing in.
    static func attention(mouse: CGPoint, typingIn window: CGRect?, sinceKey: TimeInterval, sinceMouse: TimeInterval) -> CGPoint {
        guard let window, sinceKey < sinceMouse else { return mouse }
        return CGPoint(x: window.midX, y: window.midY)
    }

    /// Lean toward where the user is working: `look` is the eyes' nudge, -1…1 each way. Right of Maxie: he leans right
    /// (a positive angle here; the view turns it clockwise).
    static func tilt(look: CGSize, reduce: Bool) -> Double {
        guard !reduce else { return 0 }
        return Double(min(max(look.width, -1), 1)) * maxTilt
    }

    /// The two spots he paces between while working: either side of `home`, or home and the one side that has room.
    /// Empty: he stays put (no room, or Reduce motion). `blocked`: the front app's windows and the open chat;
    /// `accepted`: the window the user dropped Maxie onto, fine to stroll over.
    static func walk(home: CGRect, screen: CGRect, blocked: [CGRect], accepted: CGRect? = nil, reduce: Bool) -> [CGRect] {
        guard !reduce else { return [] }
        let sides = [home.offsetBy(dx: -reach, dy: 0), home.offsetBy(dx: reach, dy: 0)].filter { spot in
            guard screen.contains(spot) else { return false }
            let cover = StepAside.cover(spot, blocked)
            return cover == nil || (accepted != nil && cover == accepted)
        }
        switch sides.count {
        case 2: return sides
        case 1: return [home, sides[0]]
        default: return []
        }
    }
}

/// Where the user is working, live and cheap enough for the eyes' 8 Hz glance: the window is looked up at most once a
/// second, and only while the user types. Typing into Maxie's own chat or Settings: that window.
@MainActor
enum Attention {
    static let windowEvery: TimeInterval = 1
    private static var window: (frame: CGRect?, at: Date)?

    static func point(now: Date = Date()) -> CGPoint {
        let sinceKey = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .keyDown)
        let sinceMouse = min(CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .mouseMoved),
                             CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .leftMouseDown))
        let mouse = NSEvent.mouseLocation
        guard sinceKey < sinceMouse else { return mouse }
        if window.map({ now.timeIntervalSince($0.at) > windowEvery }) ?? true {
            window = (NSApp.keyWindow?.frame ?? StepAside.frontWindows().first, now)
        }
        return Alive.attention(mouse: mouse, typingIn: window?.frame, sinceKey: sinceKey, sinceMouse: sinceMouse)
    }
}

/// While Maxie works, a free Maxie strolls between two spots; when he's done he walks back home.
/// While he strolls, the stroll owns where Maxie is: stepping aside and dragging stop it first.
@MainActor
final class Stroll {
    private var timer: Timer?
    private weak var window: NSWindow?
    private var spots: [CGRect] = []
    private var next = 0
    var isWalking: Bool { timer != nil }

    func start(_ window: NSWindow, spots: [CGRect]) {
        halt()
        guard spots.count == 2 else { return }
        self.window = window
        self.spots = spots
        next = 0
        step()
        let t = Timer(timeInterval: Alive.pace + Alive.pause, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.step() }
        }
        t.tolerance = 0.1
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    /// Done working: back to `home`.
    func stop(home: CGRect) {
        guard isWalking, let window else { return halt() }
        halt()
        Motion.animate(window, to: home, alpha: 1, duration: Motion.duration(Motion.step))
    }

    /// Something else takes over (a drag, stepping aside, a rebuild): stop where he is, at once.
    func halt() {
        timer?.invalidate()
        timer = nil
        if let window {
            NSAnimationContext.runAnimationGroup { ctx in  // ends the stroll's animation still in flight
                ctx.duration = 0
                window.animator().setFrame(window.frame, display: false)
            }
        }
        window = nil
    }

    private func step() {
        guard let window else { return halt() }
        Motion.animate(window, to: spots[next], alpha: 1, duration: Alive.pace, curve: CAMediaTimingFunction(name: .easeInEaseOut))
        next = 1 - next
    }
}
