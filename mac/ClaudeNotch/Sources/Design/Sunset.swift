import AppKit
import SwiftUI

/// The shared building blocks of Settings and the panel, in the "Light, not windows" language
/// (tokens in Light.swift). The names stay so every section keeps compiling, but:
/// no cards, no tiles, no borders, no pills. Groups are spacing; statuses are words in ember / signal-blue
/// / red (never green, never colour alone); buttons are words.
enum Sunset {
    // The mascot's palette, still used by Maxie and a few accents.
    static let peach = Color(hex: "#FFD9B8")
    static let coral = Light.ember
    static let indigo = Color(hex: "#3B4A7A")
    static let periwinkle = Color(hex: "#8FA3D9")
    static let gold = Light.gold

    // Meaning: ember for "do / on", signal-blue for "OK / linked", red only for errors.
    static let accent = Light.thread
    static let good = Light.ok
    static let warn = dynamic(light: "#A86A12", dark: "#FFC46B")
    static let bad = Light.bad
    // No surfaces: the light field is the only background.
    static let page = Color.clear
    static let card = Color.clear
    static let hairline = Light.words.opacity(0.08)
    static let sky = LinearGradient(colors: [Light.gold, Light.ember], startPoint: .topLeading, endPoint: .bottomTrailing)
    static let skySoft = LinearGradient(colors: [.clear], startPoint: .top, endPoint: .bottom)
    static let dusk = LinearGradient(colors: [Light.ember, Light.dusk], startPoint: .topLeading, endPoint: .bottomTrailing)

    static let unit: CGFloat = 8
    static let radiusSmall: CGFloat = 8
    static let radius: CGFloat = 12
    static let radiusLarge: CGFloat = 20
    static let spring = Light.spring

    /// Like dynamic(light:dark:), with an opacity per appearance (quiet words read on daylight cards too).
    static func dynamic(light: String, dark: String, lightAlpha: Double, darkAlpha: Double) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(Color(hex: isDark ? dark : light)).withAlphaComponent(isDark ? darkAlpha : lightAlpha)
        })
    }

    static func dynamic(light: String, dark: String) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(Color(hex: isDark ? dark : light))
        })
    }
}

/// A group of rows: a card a step off the field with a hairline, so groups read as groups.
struct SunsetCard<Content: View>: View {
    var padding: CGFloat = 16
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) { content }
            .padding(.vertical, 12).padding(.horizontal, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Light.card))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Light.edge, lineWidth: 1))
            .probe("card")
    }
}

/// A row: what it is on the left (and one quiet line about it), the control on the right.
struct SettingRow<Trailing: View>: View {
    let title: String
    var subtitle: String? = nil
    @ViewBuilder let trailing: Trailing

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Light.text)
                if let subtitle { Text(subtitle).font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true) }
            }
            Spacer(minLength: 8)
            trailing
        }
    }
}

/// Kept for callers outside Settings; draws only the glyph, quietly.
struct IconTile: View {
    let symbol: String
    var colors: [Color] = []
    var size: CGFloat = 26
    var body: some View {
        Image(systemName: symbol).font(.system(size: size * 0.5)).foregroundStyle(Light.quiet).frame(width: size, height: size)
    }
}

/// A section's title: one modest line and what it is for. Nothing large but Maxie's name.
struct SectionHeader: View {
    let section: SettingsSection

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(section.rawValue).font(.system(size: 17, weight: .semibold))
            Text(section.subtitle).font(Light.small).foregroundStyle(Light.quiet)
        }
        .padding(.bottom, 8)
    }
}

/// Yes / no as a word, coloured blue or red (the word carries the meaning).
struct StatusMark: View {
    let ok: Bool
    var yes = "On", no = "Off"
    var body: some View {
        Text(ok ? yes : no).font(.system(size: 13, weight: .medium)).foregroundStyle(ok ? Light.ok : Light.bad)
    }
}

/// A quiet line under a group ("Saved.", "Hub restarting.").
struct NoteLine: View {
    let text: String
    var symbol = ""
    var body: some View {
        if !text.isEmpty {
            Text(text).font(Light.small).foregroundStyle(Light.quiet).transition(.opacity)
        }
    }
}

/// A status word (was a pill). `symbol` is ignored: words first.
struct Chip: View {
    let text: String
    var symbol: String = ""
    var tint: Color = Light.quiet
    var body: some View {
        Text(text).font(Light.small).foregroundStyle(tint).lineLimit(1).truncationMode(.middle)
    }
}

/// Settings buttons are words (WordButtonStyle): ember and semibold for the main action, mist otherwise.
/// One normal click anywhere on the word plus its 6 pt margin fires it; nothing shrinks on press.
struct SunsetButtonStyle: ButtonStyle {
    var prominent = false
    func makeBody(configuration: Configuration) -> some View {
        WordButtonStyle(primary: prominent).makeBody(configuration: configuration)
    }
}

/// Small glyph buttons: a 28 pt square target, not just the glyph's strokes.
struct SunsetIconButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(width: 28, height: 28)
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.5 : 1)
    }
}

/// A text field with no box: a faint baseline is the only hint it takes typing.
struct LineFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .textFieldStyle(.plain)
            .font(Light.text)
            .padding(.vertical, 4)
            .overlay(alignment: .bottom) { Rectangle().fill(Light.edge).frame(height: 1) }  // the same ≥ 3:1 edge as every control
    }
}

/// Hosts SwiftUI in an AppKit window: takes the first click even when the window is not key (the notch panel
/// is usually key), and never lets a press on a control start a window drag (with the window
/// movable by its background, every plain button was a drag region, so presses got lost or needed a second
/// try). Windows move only from their own drag strips (WindowDragArea).
class FirstClickHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }
}

/// The notch panel's host: its outer few points are soft, see-through light (EdgeFalloff), so clicks there go to
/// whatever is underneath (a Settings row, the desktop) instead of the panel.
final class PanelHostingView<Content: View>: FirstClickHostingView<Content> {
    static var margin: CGFloat { 7 }

    override func hitTest(_ point: NSPoint) -> NSView? {
        let local = superview.map { convert(point, from: $0) } ?? point
        guard bounds.insetBy(dx: Self.margin, dy: Self.margin).contains(local) else { return nil }
        return super.hitTest(point)
    }
}

extension ButtonStyle where Self == SunsetButtonStyle {
    static var sunset: SunsetButtonStyle { SunsetButtonStyle() }
    static var sunsetProminent: SunsetButtonStyle { SunsetButtonStyle(prominent: true) }
}

extension ButtonStyle where Self == SunsetIconButtonStyle {
    static var sunsetIcon: SunsetIconButtonStyle { SunsetIconButtonStyle() }
}

extension TextFieldStyle where Self == LineFieldStyle {
    static var line: LineFieldStyle { LineFieldStyle() }
}
