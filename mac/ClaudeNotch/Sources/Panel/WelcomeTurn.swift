import SwiftUI

/// The first open (onboarding): Maxie waves (PanelView) and says hello as the first turn of the
/// conversation, one line after another: who he is, how to talk to him, the promise that matters, three keys.
struct WelcomeTurn: View {
    var animate = true
    let onDone: () -> Void
    @State private var appeared = false

    static let hello = "Hi, I'm Maxie."
    static let how = "Type here or say \u{201C}Hey Max\u{201D}. I work in the background and never take your screen, and I ask before I send, buy or delete anything."
    private static let keys: [(String, String)] = [(HotKeys.optionSpace.label, "open or close"), ("Esc", "close"), ("⌘,", "Settings")]
    private static let stagger = 0.07

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Knot(kind: .claude, glowing: true).padding(.top, 6)
            VStack(alignment: .leading, spacing: 8) {
                rise(0) { Text(Self.hello).font(.system(size: 17, weight: .semibold)).tracking(-0.2).clickTarget("welcome.hello") }
                rise(1) {
                    Text(Self.how).font(Light.text).lineSpacing(Light.leading).foregroundStyle(Light.quiet)
                        .fixedSize(horizontal: false, vertical: true)
                }
                rise(2) {
                    HStack(spacing: 14) {
                        ForEach(Self.keys, id: \.0) { key, what in
                            HStack(spacing: 6) {
                                KeyCap(text: key)
                                Text(what).font(Light.small).foregroundStyle(Light.quiet)
                            }
                        }
                    }
                }
                rise(3) {
                    Button("Got it", action: onDone).buttonStyle(.wordPrimary).padding(.leading, -6).clickTarget("welcome.done")
                }
            }
            .frame(maxWidth: Light.measure, alignment: .leading)
        }
        .onAppear { appeared = true }
    }

    /// Each line fades up a moment after the one before (none of that with Reduce Motion).
    private func rise<C: View>(_ i: Int, @ViewBuilder _ content: () -> C) -> some View {
        let still = !animate || Motion.reduce
        return content()
            .opacity(appeared || still ? 1 : 0)
            .offset(y: appeared || still ? 0 : 8)
            .animation(still ? nil : Light.spring.delay(Self.stagger * Double(i)), value: appeared)
    }
}

/// A key as it is printed on the keyboard: small, quiet, with a hairline edge that holds 3:1 on both themes.
struct KeyCap: View {
    let text: String
    var body: some View {
        Text(text).font(.system(size: 11, weight: .semibold)).foregroundStyle(Light.words)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 5, style: .continuous).strokeBorder(Light.edge, lineWidth: 1))
            .accessibilityLabel("key \(text)")
    }
}
