import AppKit

/// Where the panel opens and how big it is. Its default spot is next to Maxie; once the user drags it or
/// resizes it, that frame is remembered (if it still fits on a connected screen).
enum PanelLayout {
    static let originKey = "panelOrigin"
    static let heightKey = "panelHeight"
    static let defaultSize = CGSize(width: 420, height: 620)
    static let minHeight: CGFloat = 380
    static let width = defaultSize.width

    struct Saved: Equatable {
        var origin: CGPoint?
        var height: CGFloat?
    }

    static func load(_ d: UserDefaults) -> Saved {
        let o = d.array(forKey: originKey) as? [Double]
        let h = d.object(forKey: heightKey) as? Double
        return Saved(origin: o.flatMap { $0.count == 2 ? CGPoint(x: $0[0], y: $0[1]) : nil }, height: h.map { CGFloat($0) })
    }

    static func save(_ frame: CGRect, to d: UserDefaults) {
        d.set([Double(frame.minX), Double(frame.minY)], forKey: originKey)
        d.set(Double(frame.height), forKey: heightKey)
    }

    /// The frame to open at, and whether it is the user's remembered spot (then the light comes from the top).
    static func frame(saved: Saved, bot: CGRect, screen: CGRect, screens: [CGRect], atNotch: Bool) -> (frame: CGRect, moved: Bool) {
        let fallback = min(defaultSize.height, screen.height * 0.7)
        let height = clampHeight(saved.height ?? fallback, in: screen)
        let size = CGSize(width: width, height: height)
        if let o = saved.origin {
            let f = CGRect(origin: o, size: size)
            if screens.contains(where: { $0.contains(f) }) { return (f, true) }
        }
        let origin = BotGeometry.panelOrigin(bot: bot, panel: size, screen: screen, atNotch: atNotch)
        return (CGRect(origin: origin, size: size), false)
    }

    /// Double-click home: the chat goes back beside Maxie; the height the user gave it stays.
    static func forgetSpot(in d: UserDefaults) { d.removeObject(forKey: originKey) }

    /// A move the user made, not a frame Maxie set himself (opening beside him, going home).
    static func isUserMove(_ frame: CGRect, placed: CGRect?) -> Bool { frame != placed }

    static func clampHeight(_ h: CGFloat, in screen: CGRect) -> CGFloat {
        min(max(h, minHeight), screen.height)
    }

    /// Drag the bottom edge: the top stays put, the bottom never leaves the screen.
    static func resized(_ frame: CGRect, to height: CGFloat, in screen: CGRect) -> CGRect {
        let h = min(clampHeight(height, in: screen), frame.maxY - screen.minY)
        return CGRect(x: frame.minX, y: frame.maxY - h, width: frame.width, height: h)
    }
}

/// The panel's own keys.
enum PanelKey: Equatable {
    case close, settings, quit

    init?(keyCode: UInt16, characters: String?, command: Bool) {
        if keyCode == 53 { self = .close; return }  // Esc
        guard command else { return nil }
        switch characters?.lowercased() {
        case ",": self = .settings
        case "q": self = .quit
        default: return nil
        }
    }

    init?(_ e: NSEvent) {
        self.init(keyCode: e.keyCode, characters: e.charactersIgnoringModifiers,
                  command: e.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command)
    }
}
