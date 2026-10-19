import SwiftUI

/// The tiny notch bot, SwiftUI version: plays the state's pre-rendered loop at 30 fps with a live
/// eye layer on top. Reduce Motion: holds the first frame. Pauses when not visible.
/// The notch itself uses `MascotLayer` (Core Animation), which is several times cheaper; this view
/// is kept for previews and as the fallback when the layer view is not available.
struct MascotView: View {
    let state: BotState
    var visible = true
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if let loop = MascotLibrary.shared.loop(state) {
            TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion || !visible)) { ctx in
                let i = reduceMotion ? 0 : loop.frame(at: ctx.date)
                ZStack(alignment: .topLeading) {
                    Image(nsImage: loop.frames[i]).resizable().interpolation(.high)
                    if loop.liveEyes, let list = loop.meta.eye_anchors_2x, list.indices.contains(i) {
                        FaceLayer(anchors: list[i], color: Color(hex: loop.meta.eye_color ?? "#FFC48A"),
                                  glossy: loop.meta.eye_style == "glossy",
                                  date: ctx.date, followCursor: state != .think, reduceMotion: reduceMotion)
                    }
                }
                .frame(width: loop.size.width, height: loop.size.height)
            }
            .accessibilityLabel("Maxie, \(state.label)")
        } else {
            Image(systemName: "face.smiling").frame(width: 36, height: 42)  // assets missing: still show something
        }
    }
}

extension Color {
    init(hex: String) {
        let v = UInt64(hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0xFFC48A
        self.init(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }

    /// Light enough that dark marks read on it (a swatch's check).
    var isLight: Bool {
        guard let c = NSColor(self).usingColorSpace(.sRGB) else { return false }
        return 0.2126 * c.redComponent + 0.7152 * c.greenComponent + 0.0722 * c.blueComponent > 0.55
    }
}
