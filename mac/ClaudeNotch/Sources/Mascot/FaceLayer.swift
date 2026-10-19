import SwiftUI

/// Live eyes drawn over the eyeless loop frames at the per-frame anchors, nudged toward where the user works (max ~2 pt
/// at this size) and blinking every 2-5.5 s. Maxie's are glossy (dark, two highlights); the Classic robot's glow.
struct FaceLayer: View {
    let anchors: MascotLoop.Anchors
    let color: Color
    var glossy = false
    let date: Date
    var followCursor = true
    var reduceMotion = false

    var body: some View {
        let open = reduceMotion ? 1 : Blink.shared.openness(at: date)
        let look = followCursor ? CursorLook.offset() : .zero
        Canvas { ctx, _ in
            for eye in [anchors.left, anchors.right] {
                let w = eye.w / 2, h = max(0.6, eye.h / 2 * open)          // anchors are 2x pixels
                let rect = CGRect(x: eye.x / 2 - w / 2 + look.width, y: eye.y / 2 - h / 2 + look.height, width: w, height: h)
                if glossy {
                    ctx.fill(Path(ellipseIn: rect), with: .color(color))
                    ctx.fill(Path(ellipseIn: GlossyEye.iris(in: rect)), with: .color(GlossyEye.irisColor.opacity(GlossyEye.irisOpacity)))
                    if open > 0.5 {
                        for (dx, dy, k) in GlossyEye.highlights {
                            ctx.fill(Path(ellipseIn: GlossyEye.spot(in: rect, dx: dx, dy: dy, k: k)), with: .color(.white))
                        }
                    }
                    continue
                }
                ctx.drawLayer { layer in  // one glow per eye (filters on ctx would stack)
                    layer.addFilter(.shadow(color: color.opacity(0.9), radius: 1.2))
                    layer.fill(Path(roundedRect: rect, cornerRadius: min(w, h) / 2), with: .color(color))
                }
            }
        }
        .allowsHitTesting(false)
    }
}

/// Where Maxie's two eye highlights sit, as fractions of the eye (the big one upper left, a small one lower right).
enum GlossyEye {
    static let highlights: [(Double, Double, Double)] = [(0.2, 0.16, 0.34), (0.62, 0.62, 0.15)]
    /// The warm iris glow in the lower half, as in the rendered Mochi eyes.
    static let irisColor = Color(hex: "#6B4330")
    static let irisOpacity = 0.6

    static func iris(in r: CGRect) -> CGRect {
        CGRect(x: r.minX + r.width * 0.16, y: r.minY + r.height * 0.46, width: r.width * 0.68, height: r.height * 0.44)
    }

    static func spot(in r: CGRect, dx: Double, dy: Double, k: Double) -> CGRect {
        let d = r.width * k
        return CGRect(x: r.minX + r.width * dx, y: r.minY + r.height * dy, width: d, height: d)
    }
}

/// Blink timing shared by every face (one blink clock for the app).
@MainActor
final class Blink {
    static let shared = Blink()
    private var next = Date().addingTimeInterval(2)
    private var started: Date?

    func openness(at now: Date) -> Double {
        if started == nil, now >= next { started = now }
        guard let s = started else { return 1 }
        let p = now.timeIntervalSince(s) / 0.13
        if p >= 1 {
            started = nil
            next = now.addingTimeInterval(Double.random(in: 0...1) < 0.2 ? 0.22 : .random(in: 2...5.5))
            return 1
        }
        return abs(cos(p * .pi))
    }
}

/// Where the user is working (Attention: the pointer, or the window they type in) relative to the notch bot, as a small
/// eye offset in points.
@MainActor
enum CursorLook {
    static var origin: CGPoint = .zero  // Maxie's center on screen, set by NotchController
    /// The most the eyes move, at the notch's size: 1 pt read as "just blinking".
    static let reach = 1.8

    static func offset(max m: Double = reach) -> CGSize { offset(to: Attention.point(), from: origin, max: m) }

    static func offset(to p: CGPoint, from origin: CGPoint, max m: Double) -> CGSize {
        let dx = p.x - origin.x, dy = origin.y - p.y   // screen y goes up; view y goes down
        let d = max(hypot(dx, dy), 1)
        let k = min(d / 300, 1) * m
        return CGSize(width: dx / d * k, height: dy / d * k)
    }
}
