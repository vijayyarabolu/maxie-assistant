import Foundation

/// Where Maxie lives on screen: at the notch (home), tucked into a screen edge, or anywhere.
/// Pure geometry so it can be unit-tested; NotchController applies it to the panels.
/// Coordinates are AppKit screen coordinates (origin bottom-left, y up).
struct BotPlacement: Codable, Equatable {
    enum Mode: String, Codable { case notch, free, tuckedLeft, tuckedRight }

    var mode: Mode
    var x: Double   // origin of Maxie's window when free; the y of the tuck when tucked
    var y: Double
    var displayID: UInt32

    static func notch(on display: UInt32) -> BotPlacement { BotPlacement(mode: .notch, x: 0, y: 0, displayID: display) }

    var isAtNotch: Bool { mode == .notch }
    var isTucked: Bool { mode == .tuckedLeft || mode == .tuckedRight }
}

enum BotGeometry {
    static let snapDistance: CGFloat = 28     // this close to an edge or to home: snap
    static let peek: CGFloat = 0.45           // the fraction of Maxie that stays visible when tucked
    static let panelGap: CGFloat = 8

    /// Keep the whole bot on screen (the off-screen guard).
    static func clamp(_ origin: CGPoint, size: CGSize, in screen: CGRect) -> CGPoint {
        CGPoint(x: min(max(origin.x, screen.minX), screen.maxX - size.width),
                y: min(max(origin.y, screen.minY), screen.maxY - size.height))
    }

    /// Where a drop lands: home when near the notch, a tuck when near a side edge (corners count as
    /// edges), otherwise a free spot clamped on screen.
    static func snap(drop origin: CGPoint, size: CGSize, in screen: CGRect, home: CGRect, display: UInt32) -> BotPlacement {
        let p = clamp(origin, size: size, in: screen)
        let centre = CGPoint(x: p.x + size.width / 2, y: p.y + size.height / 2)
        if abs(centre.x - home.midX) <= snapDistance * 2, abs(centre.y - home.midY) <= snapDistance * 2 {
            return .notch(on: display)
        }
        if p.x - screen.minX <= snapDistance {
            return BotPlacement(mode: .tuckedLeft, x: 0, y: p.y, displayID: display)
        }
        if screen.maxX - (p.x + size.width) <= snapDistance {
            return BotPlacement(mode: .tuckedRight, x: 0, y: p.y, displayID: display)
        }
        return BotPlacement(mode: .free, x: p.x, y: p.y, displayID: display)
    }

    /// Maxie window's frame for a placement. A tucked bot shows only `peek` of itself unless `peeking`
    /// (hover) slides it fully out.
    static func frame(for placement: BotPlacement, size: CGSize, in screen: CGRect, home: CGRect, peeking: Bool = false) -> CGRect {
        switch placement.mode {
        case .notch:
            return home
        case .free:
            return CGRect(origin: clamp(CGPoint(x: placement.x, y: placement.y), size: size, in: screen), size: size)
        case .tuckedLeft:
            let y = clamp(CGPoint(x: screen.minX, y: placement.y), size: size, in: screen).y
            let x = peeking ? screen.minX : screen.minX - size.width * (1 - peek)
            return CGRect(x: x, y: y, width: size.width, height: size.height)
        case .tuckedRight:
            let y = clamp(CGPoint(x: screen.minX, y: placement.y), size: size, in: screen).y
            let x = peeking ? screen.maxX - size.width : screen.maxX - size.width * peek
            return CGRect(x: x, y: y, width: size.width, height: size.height)
        }
    }

    /// The panel opens next to Maxie and never off screen: below it when there is room, else above, else
    /// beside it (right, then left), so it never covers him; at the notch, centred under the notch as before.
    static func panelOrigin(bot: CGRect, panel: CGSize, screen: CGRect, atNotch: Bool) -> CGPoint {
        let below = CGPoint(x: bot.midX - panel.width / 2, y: bot.minY - panelGap - panel.height)
        if atNotch { return below.clampedOrigin(size: panel, in: screen) }
        let above = CGPoint(x: bot.midX - panel.width / 2, y: bot.maxY + panelGap)
        for c in [below, above] where c.y >= screen.minY && c.y + panel.height <= screen.maxY {
            return c.clampedOrigin(size: panel, in: screen)
        }
        let right = CGPoint(x: bot.maxX + panelGap, y: bot.maxY - panel.height)
        let left = CGPoint(x: bot.minX - panelGap - panel.width, y: bot.maxY - panel.height)
        for c in [right, left] where c.x >= screen.minX && c.x + panel.width <= screen.maxX {
            return c.clampedOrigin(size: panel, in: screen)
        }
        return below.clampedOrigin(size: panel, in: screen)
    }
}

private extension CGPoint {
    func clampedOrigin(size: CGSize, in screen: CGRect) -> CGPoint {
        BotGeometry.clamp(self, size: size, in: screen)
    }
}

/// Saved placement per display (UserDefaults), restored after a restart, sleep or a display change.
struct PlacementStore {
    let defaults: UserDefaults
    let key = "botPlacements"

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    func save(_ p: BotPlacement) {
        var all = load()
        all[String(p.displayID)] = p
        if let data = try? JSONEncoder().encode(all) { defaults.set(data, forKey: key) }
    }

    func load() -> [String: BotPlacement] {
        guard let data = defaults.data(forKey: key),
              let all = try? JSONDecoder().decode([String: BotPlacement].self, from: data) else { return [:] }
        return all
    }

    /// The placement to use now: the one saved for a connected display, else the notch on the main display.
    func restore(connected: [UInt32], main: UInt32) -> BotPlacement {
        let all = load()
        for id in connected {
            if let p = all[String(id)] { return p }
        }
        return .notch(on: main)
    }

    func reset() { defaults.removeObject(forKey: key) }
}

/// Which displays are attached and how big they are: what decides whether Maxie's window needs rebuilding.
struct ScreenLayout: Equatable {
    static let settle: Double = 0.3  // notifications come in bursts; act once they stop
    let screens: [String]

    init(_ screens: [(UInt32, CGRect)]) {
        self.screens = screens.map { id, f in "\(id):\(Int(f.minX)),\(Int(f.minY)),\(Int(f.width))x\(Int(f.height))" }.sorted()
    }
}
