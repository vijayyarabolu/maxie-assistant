import SwiftUI

/// Settings controls you can see: "light, not windows" went too far, so in Settings every control
/// has an edge or fill at ≥ 3:1 against the field, and cards are a step lighter (darker in daylight) with a
/// hairline. The panel keeps its pure light.
extension View {
    /// A drop-down picker, a field: a hairline box around it.
    func boxed() -> some View {
        padding(.horizontal, 8).padding(.vertical, 3)
            .background(RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(Light.edge, lineWidth: 1))
            .probe("control")
    }
}

/// A switch drawn the same whether or not the window is key (the system one greys out "on" in a window that
/// isn't key, so on and off differed only by the knob). On: a filled thread track, knob right. Off: an
/// edged, empty track, knob left. Never colour alone: fill and knob position both change.
struct EdgedSwitch: ToggleStyle {
    private static let track = CGSize(width: 40, height: 24)
    private static let inset: CGFloat = 3

    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 8) {
            configuration.label
            Button { configuration.isOn.toggle() } label: { track(isOn: configuration.isOn) }
                .buttonStyle(.plain)
                .accessibilityAddTraits(.isToggle)
                .accessibilityValue(configuration.isOn ? "on" : "off")
                .probe("control")
        }
    }

    private func track(isOn: Bool) -> some View {
        let knob = Self.track.height - Self.inset * 2
        return ZStack(alignment: isOn ? .trailing : .leading) {
            Capsule().fill(isOn ? Light.thread : Color.clear)
            Capsule(style: .circular).strokeBorder(isOn ? Light.thread : Light.edge, lineWidth: 1)
            Circle().fill(isOn ? Light.onThread : Light.edge)
                .frame(width: knob, height: knob)
                .padding(Self.inset)
        }
        .frame(width: Self.track.width, height: Self.track.height)
        .contentShape(Capsule())
        .animation(.easeOut(duration: 0.12), value: isOn)
    }
}
