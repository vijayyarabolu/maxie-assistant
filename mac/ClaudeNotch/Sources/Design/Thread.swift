import SwiftUI

/// The Thread (the design's signature): one living line of light down the left of Activity and Memory, the
/// agent's pulse. The chat dropped it: its turns are dots on their own.
/// idle: faint, slow 4 s breath. listening: brighter and wider, following the mic level. thinking: a soft
/// pulse travels down it. speaking: it glows at the current sentence (its knot). Reduced motion: no pulse,
/// the glow stays.
enum ThreadMode: Equatable {
    case idle, listening, thinking, speaking
}

struct ThreadLine: View {
    let mode: ThreadMode
    var level: Double = 0  // 0...1, the mic while listening
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 120, paused: reduceMotion)) { ctx in
            Canvas { g, size in
                draw(&g, size: size, t: reduceMotion ? 0 : ctx.date.timeIntervalSinceReferenceDate)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private func draw(_ g: inout GraphicsContext, size: CGSize, t: Double) {
        let breath = reduceMotion ? 0.5 : 0.5 + 0.5 * sin(t * 2 * .pi / 4)
        let (alpha, width): (Double, CGFloat) = switch mode {
        case .idle: (0.18 + 0.14 * breath, 1.5)
        case .listening: (0.55 + 0.4 * level, 2 + 2.5 * CGFloat(level))
        case .thinking: (0.3, 1.5)
        case .speaking: (0.45, 2)
        }
        let x = size.width / 2
        let line = Path(CGRect(x: x - width / 2, y: 0, width: width, height: size.height))
        let fade = Gradient(stops: [.init(color: Light.thread.opacity(0), location: 0), .init(color: Light.thread, location: 0.04),
                                    .init(color: Light.thread, location: 0.92), .init(color: Light.thread.opacity(0), location: 1)])
        var lineCtx = g
        lineCtx.opacity = alpha
        lineCtx.fill(line, with: .linearGradient(fade, startPoint: .zero, endPoint: CGPoint(x: 0, y: size.height)))
        if mode == .listening || mode == .speaking {  // a soft halo around the line
            var glow = g
            glow.addFilter(.blur(radius: 6))
            glow.opacity = alpha * 0.6
            glow.fill(Path(CGRect(x: x - width * 2, y: 0, width: width * 4, height: size.height)), with: .color(Light.thread))
        }
        if mode == .thinking && !reduceMotion {  // the pulse travelling down
            let period = 1.6
            let p = (t.truncatingRemainder(dividingBy: period)) / period
            let y = CGFloat(p) * (size.height + 80) - 40
            var pulse = g
            pulse.addFilter(.blur(radius: 5))
            pulse.fill(Path(ellipseIn: CGRect(x: x - 4, y: y - 28, width: 8, height: 56)), with: .color(Light.thread))
        }
    }
}

/// A knot on the Thread: every turn, task step and memory fact is one.
struct Knot: View {
    enum Kind { case claude, user, step(ok: Bool), fact, thinking }
    let kind: Kind
    var glowing = false  // the sentence being spoken; a fact just remembered
    @State private var flash = false

    var body: some View {
        ZStack {
            switch kind {
            case .claude:
                Circle().fill(Light.thread).frame(width: 7, height: 7)
            case .user:
                Circle().stroke(Light.userWords, lineWidth: 1.2).frame(width: 6, height: 6)
            case .step(let ok):
                if ok { Circle().fill(Light.thread.opacity(0.75)).frame(width: 5, height: 5) }
                else { Circle().stroke(Light.bad, lineWidth: 1.5).frame(width: 6, height: 6) }
            case .fact:
                Circle().fill(Light.gold).frame(width: 5, height: 5)
            case .thinking:
                Circle().stroke(Light.thread, style: StrokeStyle(lineWidth: 1.2, dash: [2, 2])).frame(width: 8, height: 8)
            }
        }
        .shadow(color: (glowing ? Light.gold : .clear).opacity(flash ? 0.9 : 0.0), radius: 6)
        .shadow(color: glowing ? Light.thread.opacity(0.8) : .clear, radius: glowing ? 5 : 0)
        .frame(width: 14, height: 14)
        .onAppear {
            guard glowing else { return }
            flash = true
            withAnimation(.easeOut(duration: 1.6)) { flash = false }
        }
        .accessibilityHidden(true)
    }
}
