import AppKit

/// The menu-bar icon, drawn in code as a template image so it follows the menu bar's colour.
/// Maxie (the default): Mochi's round head with round ears and two big eyes. Classic: the Classic robot's head.
/// Asleep (hub offline): closed eyes. Paused: a slash.
enum BotGlyph {
    static let size = NSSize(width: 18, height: 16)

    static func image(asleep: Bool = false, muted: Bool = false, classic: Bool = MaxieLook.character() == .classic) -> NSImage {
        let img = NSImage(size: size, flipped: false) { _ in
            NSColor.black.set()
            if classic { classicHead(asleep: asleep) } else { maxie(asleep: asleep) }
            if muted {
                let slash = NSBezierPath()
                slash.move(to: NSPoint(x: 1, y: 0.5))
                slash.line(to: NSPoint(x: 17, y: 15))
                slash.lineWidth = 1.5
                slash.stroke()
            }
            return true
        }
        img.isTemplate = true
        img.accessibilityDescription = "Maxie"
        return img
    }

    /// A round head (12 pt), two round ears on top, two big round eyes.
    private static func maxie(asleep: Bool) {
        for x in [2.6, 11.2] { NSBezierPath(ovalIn: NSRect(x: x, y: 10.4, width: 4.2, height: 4.2)).fill() }
        let head = NSBezierPath(ovalIn: NSRect(x: 3.2, y: 0.9, width: 11.6, height: 11.2))
        head.lineWidth = 1.5
        head.stroke()
        eyes(at: [6.1, 9.9], y: 4.6, size: NSSize(width: 2, height: 2.6), asleep: asleep)
    }

    private static func classicHead(asleep: Bool) {
        let face = NSBezierPath(roundedRect: NSRect(x: 2, y: 1, width: 14, height: 10.5), xRadius: 4, yRadius: 4)
        face.lineWidth = 1.5
        face.stroke()
        let stalk = NSBezierPath()
        stalk.move(to: NSPoint(x: 9, y: 11.5))
        stalk.line(to: NSPoint(x: 9, y: 13.5))
        stalk.lineWidth = 1.4
        stalk.stroke()
        NSBezierPath(ovalIn: NSRect(x: 7.6, y: 13, width: 2.8, height: 2.8)).fill()
        eyes(at: [5.6, 10.4], y: 4.6, size: NSSize(width: 2, height: 3), asleep: asleep)
    }

    private static func eyes(at xs: [Double], y: Double, size: NSSize, asleep: Bool) {
        for x in xs {
            if asleep {
                let lid = NSBezierPath()
                lid.move(to: NSPoint(x: x, y: y + 1.4))
                lid.line(to: NSPoint(x: x + 2, y: y + 1.4))
                lid.lineWidth = 1.4
                lid.stroke()
            } else {
                NSBezierPath(ovalIn: NSRect(origin: NSPoint(x: x, y: y), size: size)).fill()
            }
        }
    }
}
