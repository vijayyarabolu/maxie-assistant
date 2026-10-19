import AppKit

/// The off-screen hover record: the pointer brushing Maxie's edge (50 crossings), resting on him with
/// jitter at his edge (50 more), then leaving. Each row shows when hover says "on him": raw enter and exit as
/// before, and HoverGate now. Every change in a row was a jump (the chat opening, a tucked Maxie sliding).
enum HoverTrace {
    struct Sample {
        let x: CGFloat
        let t: TimeInterval
    }

    static let bot = CGRect(x: 800, y: 919, width: 36, height: 42)
    static let step: TimeInterval = 0.02

    static func samples() -> [Sample] {
        let brush = (0..<50).map { n in Sample(x: bot.minX + (n.isMultiple(of: 2) ? 3 : -3), t: Double(n) * step * 2) }
        let rest = (0..<25).map { n in Sample(x: bot.midX, t: 2 + Double(n) * step) }
        let jitter = (0..<50).map { n in Sample(x: bot.minX + (n.isMultiple(of: 2) ? 3 : -3), t: 2.5 + Double(n) * step * 2) }
        let away = (0..<25).map { n in Sample(x: bot.minX - 40, t: 4.5 + Double(n) * step) }
        return brush + rest + jitter + away
    }

    /// (raw, gated) "on him" for every sample.
    static func rows() -> [(Bool, Bool)] {
        var gate = HoverGate()
        return samples().map { s in
            let p = CGPoint(x: s.x, y: bot.midY)
            _ = gate.update(p, frame: bot, now: s.t)
            return (bot.contains(p), gate.inside)
        }
    }

    static func changes(_ row: [Bool]) -> Int { zip(row, row.dropFirst()).filter { $0 != $1 }.count }

    static func write(to url: URL) {
        let rows = rows(), raw = rows.map(\.0), gated = rows.map(\.1)
        let size = NSSize(width: 1000, height: 200), left: CGFloat = 220, w = (size.width - left - 20) / CGFloat(rows.count)
        let img = NSImage(size: size, flipped: true) { _ in
            NSColor(white: 0.1, alpha: 1).setFill()
            NSRect(origin: .zero, size: size).fill()
            let font: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.white]
            for (n, (title, row, color)) in [("before: raw enter/exit", raw, NSColor.systemRed),
                                              ("now: dead zone + settle", gated, NSColor.systemGreen)].enumerated() {
                let y = 40 + CGFloat(n) * 70
                ("\(title)\n\(changes(row)) changes" as NSString).draw(at: NSPoint(x: 12, y: y), withAttributes: font)
                NSColor(white: 0.25, alpha: 1).setFill()
                NSRect(x: left, y: y, width: w * CGFloat(row.count), height: 36).fill()
                color.setFill()
                for (i, on) in row.enumerated() where on { NSRect(x: left + CGFloat(i) * w, y: y, width: w, height: 36).fill() }
            }
            ("brushing his edge ×50 · on him · jitter at his edge ×50 · away" as NSString)
                .draw(at: NSPoint(x: left, y: 14), withAttributes: font)
            return true
        }
        guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return }
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}
