import AppKit
import SwiftUI

/// "Light, not windows". Maxie is light that lives in the notch and spills out when
/// called: no frame, no card, no border, no bubbles. Words, Maxie, and light. The Sunset palette
/// is the light source; the field is night (daylight in light mode: mist to gold, words in night).
/// Red-green safe: ember, gold and signal-blue carry meaning; red only for errors, never green.
enum Light {
    static let night = Color(hex: "#0E0B14")
    static let dusk = Color(hex: "#2A1B3D")
    static let ember = Color(hex: "#FF7A59")
    static let gold = Color(hex: "#FFC46B")
    static let rose = Color(hex: "#F2A1B5")
    static let mist = Color(hex: "#F5EFE6")
    static let signal = Color(hex: "#5AB4FF")
    static let error = Color(hex: "#FF4D4D")

    /// Maxie's words: mist on the night field, night in daylight. Never pure white.
    static let words = Sunset.dynamic(light: "#1A1422", dark: "#F5EFE6")
    /// His words: rose (deeper in daylight so it reads on mist).
    static let userWords = Sunset.dynamic(light: "#A8456A", dark: "#F2A1B5")
    /// The Thread and the primary action (a touch deeper in daylight).
    static let thread = Sunset.dynamic(light: "#C2492B", dark: "#FF7A59")  // ≥ 3:1 on daylight cards
    static let ok = Sunset.dynamic(light: "#1F6FB8", dark: "#5AB4FF")
    static let notice = Sunset.dynamic(light: "#8A5A00", dark: "#FFC46B")  // gold words that wait on the user; ≥ 4.5:1 on daylight
    static let bad = Sunset.dynamic(light: "#D12F2F", dark: "#FF4D4D")
    /// So borders and colours never merge: a hairline at ≥ 3:1 against the field and against a card, for
    /// every control's edge and every card's outline; a card fill ≥ 1.25:1 against the field (ContrastTests).
    static let edge = Sunset.dynamic(light: "#6E6772", dark: "#8A8494")
    static let card = Sunset.dynamic(light: "#D6CFC7", dark: "#2E2A36")
    /// Text on the ember fill of a chosen option.
    static let onThread = Sunset.dynamic(light: "#FFFFFF", dark: "#160F1C")
    static let quiet = Sunset.dynamic(light: "#1A1422", dark: "#F5EFE6", lightAlpha: 0.74, darkAlpha: 0.62)  // ≥ 4.5:1 on field and cards
    static let faint = Sunset.dynamic(light: "#1A1422", dark: "#F5EFE6", lightAlpha: 0.7, darkAlpha: 0.58)    // the tabs you are not on

    // Type: SF Pro only; roles by weight and size. 14 pt words on 20 pt lines, at most ~60 characters a
    // line: a panel, not a page.
    static let text = Font.system(size: 14)
    static let userText = Font.system(size: 14, weight: .medium)
    static let small = Font.system(size: 12)
    static let label = Font.system(size: 12, weight: .semibold)
    static let name = Font.system(size: 22, weight: .semibold)
    static let leading: CGFloat = 3
    static let measure: CGFloat = 400  // ~60 characters of 14 pt SF
    static let gutter: CGFloat = 16
    static let spring = Animation.spring(response: 0.35, dampingFraction: 0.8)
}

/// The field of light behind everything, readable over anything: strong system blur behind
/// the window, a 90% base of night (mist in daylight) so no desktop text shows through the words, the
/// sunset glow only around Maxie at the top, and a narrow fall-off at the outer edge (never a line).
struct LightField: View {
    /// Where the light comes from (the notch, or Maxie when the user has dragged him elsewhere).
    var source: UnitPoint = .top
    var reach: CGFloat = 560
    @Environment(\.colorScheme) private var scheme

    /// Behind the words: ≥ 88% opaque, so body text keeps 7:1 over white, Terminal or a photo.
    static let baseOpacity = 0.9

    var body: some View {
        ZStack {
            GlassBehind(light: scheme == .light)
            (scheme == .dark ? Light.night : Light.mist).opacity(Self.baseOpacity)
            RadialGradient(stops: scheme == .dark ? Self.night : Self.day, center: source, startRadius: 0, endRadius: min(reach, 240))
                .opacity(0.45)
        }
        .mask(EdgeFalloff())
        .allowsHitTesting(false)
    }

    static let night: [Gradient.Stop] = [.init(color: Light.gold, location: 0), .init(color: Light.ember, location: 0.18),
                                         .init(color: Light.dusk, location: 0.55), .init(color: Light.dusk.opacity(0), location: 1)]
    static let day: [Gradient.Stop] = [.init(color: Light.gold, location: 0), .init(color: Light.gold.opacity(0.5), location: 0.3),
                                       .init(color: Light.gold.opacity(0), location: 1)]
}

/// Opaque in the middle, transparent only at the outer edge: the panel ends in light, not a border.
struct EdgeFalloff: View {
    var inset: CGFloat = 1
    var softness: CGFloat = 6
    var body: some View {
        Rectangle().padding(inset).blur(radius: softness)
    }
}

/// System glass behind the window (a strong blur of the desktop): .hudWindow at night, .popover in
/// daylight. Its edges are feathered with a resizable mask image (AppKit applies it on screen; a SwiftUI
/// mask does not reach the window-server blur). Plain snapshots leave it out (the window server can't
/// draw it into a PNG); backdrop shots keep it, since they capture the real windows.
struct GlassBehind: View {
    var light = false

    var body: some View {
        let env = ProcessInfo.processInfo.environment
        if env["CLAUDE_NOTCH_SNAPSHOT"] == nil || env["CLAUDE_NOTCH_BACKDROPS"] != nil { Glass(light: light) } else { Color.clear }
    }

    private struct Glass: NSViewRepresentable {
        let light: Bool

        func makeNSView(context: Context) -> NSVisualEffectView {
            let v = NSVisualEffectView()
            v.blendingMode = .behindWindow
            v.state = .active
            v.maskImage = Self.feather
            updateNSView(v, context: context)
            return v
        }

        func updateNSView(_ v: NSVisualEffectView, context: Context) {
            v.material = light ? .popover : .hudWindow
        }

        /// Opaque centre, edges falling off to clear over 8 pt; stretches to any size.
        static let feather: NSImage = {
            let edge: CGFloat = 8, side = edge * 2 + 1
            let img = NSImage(size: NSSize(width: side, height: side), flipped: false) { rect in
                let steps = Int(edge)
                for i in 0..<steps {
                    let t = CGFloat(i) / CGFloat(steps)
                    NSColor.black.withAlphaComponent(t * t * (3 - 2 * t)).setFill()  // smoothstep
                    NSBezierPath(roundedRect: rect.insetBy(dx: CGFloat(i), dy: CGFloat(i)), xRadius: edge - CGFloat(i), yRadius: edge - CGFloat(i)).fill()
                }
                return true
            }
            img.capInsets = NSEdgeInsets(top: edge, left: edge, bottom: edge, right: edge)
            img.resizingMode = .stretch
            return img
        }()
    }
}

/// A word that acts: no box, no border. Ember when it is the main action, mist otherwise; brightens on
/// hover. The whole word plus 6 pt around it takes the click.
struct WordButtonStyle: ButtonStyle {
    var primary = false
    var danger = false
    func makeBody(configuration: Configuration) -> some View {
        WordLabel(configuration: configuration, primary: primary, danger: danger)
    }

    private struct WordLabel: View {
        let configuration: ButtonStyleConfiguration
        let primary: Bool
        let danger: Bool
        @State private var hover = false
        @Environment(\.isEnabled) private var enabled

        @Environment(\.edgedControls) private var edged

        var body: some View {
            let tint = danger ? Light.bad : primary ? Light.thread : Light.words
            configuration.label
                .labelStyle(.titleOnly)
                .font(.system(size: 13, weight: primary ? .semibold : .regular))
                .foregroundStyle(tint.opacity(!enabled ? 0.3 : configuration.isPressed ? 0.6 : (hover || primary || danger) ? 1 : 0.85))
                .padding(.vertical, 4).padding(.horizontal, edged ? 9 : 6)
                .background {
                    if edged { RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(primary ? Light.thread : Light.edge, lineWidth: 1) }
                }
                .contentShape(Rectangle())
                .onHover { hover = $0 }
                .probe("control")
        }
    }
}

extension ButtonStyle where Self == WordButtonStyle {
    static var word: WordButtonStyle { WordButtonStyle() }
    static var wordPrimary: WordButtonStyle { WordButtonStyle(primary: true) }
    static var wordDanger: WordButtonStyle { WordButtonStyle(danger: true) }
}

/// A 20 pt glyph at 55% that lights up to ember on hover (mic, screen). Always has a spoken label.
struct GlyphButton: View {
    let symbol: String
    let label: String
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 15, weight: .regular))
                .frame(width: 20, height: 20)
                .foregroundStyle(hover ? Light.thread : Light.quiet)
                .contentShape(Rectangle().inset(by: -6))
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(label)
        .accessibilityLabel(label)
    }
}

/// A choice between a few words (Allow / Ask me / Block): the chosen one in ember, the rest quiet. No
/// segmented box. Each word is a button with its own label, so VoiceOver reads the options.
struct WordChoice<Value: Hashable>: View {
    let options: [(String, Value)]
    @Binding var selection: Value
    var id = "word"  // click-test ids: "<id>.<label>"

    var body: some View {
        HStack(spacing: 2) {
            ForEach(Array(options.enumerated()), id: \.offset) { _, option in
                let chosen = option.1 == selection
                Button { selection = option.1 } label: {  // the whole pill is the button, not just its word
                    Text(option.0)
                        .font(.system(size: 13, weight: chosen ? .semibold : .regular))
                        .foregroundStyle(chosen ? Light.onThread : Light.words)
                        .padding(.vertical, 4).padding(.horizontal, 8)
                        .background(Capsule().fill(chosen ? Light.thread : .clear))  // chosen: a filled pill and semibold, not colour alone
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                    .accessibilityAddTraits(chosen ? .isSelected : [])
                    .clickTarget("\(id).\(option.0)")
            }
        }
        .padding(2)
        .overlay(Capsule(style: .circular).strokeBorder(Light.edge, lineWidth: 1))
        .probe("control")
    }
}

/// Settings turns control edges on; the panel stays pure light.
private struct EdgedControlsKey: EnvironmentKey { static let defaultValue = false }

extension EnvironmentValues {
    var edgedControls: Bool {
        get { self[EdgedControlsKey.self] }
        set { self[EdgedControlsKey.self] = newValue }
    }
}
