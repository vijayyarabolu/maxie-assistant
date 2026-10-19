import AppKit

/// Nothing Maxie shows ever covers what the user is working on. When the front app's window sits under
/// a free-floating Maxie, he steps to the nearest clear spot beside it (or peeks from the screen edge when there is
/// none); a full-screen app, or a window over the notch, sends him up and out. When it's gone, he comes back home.
/// Pure: the controller feeds it window frames (CGWindowList) and moves the real window.
enum StepAside {
    enum Move: Equatable { case stay, aside(CGRect), hide }

    static let covering: CGFloat = 0.25  // a window over this share of Maxie covers him
    static let gap: CGFloat = 8          // how far he stands from the window's edge
    static let peek: CGFloat = 0.3       // with no room, this much of him stays on screen

    /// `accepted`: the window the user dropped Maxie onto. That one never moves him (stepping
    /// off it sent Maxie back to the screen edge after every drag); a window that comes over him later still does.
    static func move(bot home: CGRect, windows: [CGRect], fullscreen: Bool, atNotch: Bool, screen: CGRect,
                     accepted: CGRect? = nil) -> Move {
        if fullscreen { return .hide }
        guard let w = cover(home, windows), w != accepted else { return .stay }
        if atNotch { return .hide }  // the notch is his only home there: out of sight until it's clear
        let spots = [CGRect(origin: CGPoint(x: w.maxX + gap, y: home.minY), size: home.size),
                     CGRect(origin: CGPoint(x: w.minX - gap - home.width, y: home.minY), size: home.size),
                     CGRect(origin: CGPoint(x: home.minX, y: w.maxY + gap), size: home.size),
                     CGRect(origin: CGPoint(x: home.minX, y: w.minY - gap - home.height), size: home.size)]
        let clear = spots.filter { screen.contains($0) && cover($0, windows) == nil }
        if let best = clear.min(by: { distance($0, home) < distance($1, home) }) { return .aside(best) }
        let right = screen.maxX - home.midX < home.midX - screen.minX
        let x = right ? screen.maxX - home.width * peek : screen.minX - home.width * (1 - peek)
        return .aside(CGRect(origin: CGPoint(x: x, y: home.minY), size: home.size))
    }

    /// The first window over at least a quarter of `bot`.
    static func cover(_ bot: CGRect, _ windows: [CGRect]) -> CGRect? {
        let area = bot.width * bot.height
        guard area > 0 else { return nil }
        return windows.first { w in
            let i = w.intersection(bot)
            return !i.isNull && i.width * i.height >= area * covering
        }
    }

    /// CGWindowList bounds (top-left origin) as AppKit screen coordinates (bottom-left origin).
    static func cocoa(_ r: CGRect, primaryHeight: CGFloat) -> CGRect {
        CGRect(x: r.minX, y: primaryHeight - r.minY - r.height, width: r.width, height: r.height)
    }

    private static func distance(_ a: CGRect, _ b: CGRect) -> CGFloat {
        hypot(a.midX - b.midX, a.midY - b.midY)
    }

    /// The front app's normal windows, on screen now, in AppKit coordinates. Never Maxie's own.
    static func frontWindows() -> [CGRect] {
        guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier, pid != getpid(),
              let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]
        else { return [] }
        let top = NSScreen.screens.first?.frame.height ?? 0
        return list.compactMap { w in
            guard (w[kCGWindowLayer as String] as? Int) == 0, (w[kCGWindowOwnerPID as String] as? Int32) == pid,
                  let b = w[kCGWindowBounds as String] as? [String: CGFloat],
                  let x = b["X"], let y = b["Y"], let width = b["Width"], let height = b["Height"] else { return nil }
            return cocoa(CGRect(x: x, y: y, width: width, height: height), primaryHeight: top)
        }
    }
}
