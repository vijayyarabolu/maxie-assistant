import AppKit
import SwiftUI

/// when Maxie heard something and won't act on it, or can't hear at all, the notch says so in words with
/// an icon for a few seconds, never just silence. The voice helper sends most of them (`voice_notice`); the app adds
/// "Voice service stopped" (the hub's heartbeat) and "Can't reach Maxie's hub".
struct VoiceNotice: Equatable, Identifiable {
    let id = UUID()
    let kind: String
    let text: String

    var icon: String { Self.icons[kind] ?? "waveform" }

    static let seconds: Double = 4
    static let icons: [String: String] = [
        "not_your_voice": "person.crop.circle.badge.questionmark",
        "not_enrolled": "person.wave.2",
        "didnt_catch": "ear.trianglebadge.exclamationmark",
        "mic_silent": "mic.slash",
        "voice_down": "waveform.slash",
        "hub_down": "bolt.horizontal.circle",
        "crash_loop": "exclamationmark.triangle",
    ]
    static let voiceDown = "Voice service stopped. Restarting it…"
    static let hubDown = "Can't reach Maxie's hub."
}

/// One line on the night colour: the icon in gold, the words in mist (≥ 4.5:1 either way).
struct VoiceNoticeView: View {
    let notice: VoiceNotice

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: notice.icon)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Light.gold)
            Text(notice.text)
                .font(Light.small)
                .foregroundStyle(Light.mist)
                .lineLimit(2)
                .frame(maxWidth: NoticeLine.maxTextWidth, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(Capsule().fill(Light.night.opacity(0.92)))
        .fixedSize()
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Maxie: \(notice.text)")
    }
}

/// The window under Maxie that shows a notice: mouse-transparent, never key, gone after `VoiceNotice.seconds`.
@MainActor
final class NoticeLine {
    static let maxTextWidth: CGFloat = 260
    static let gap: CGFloat = 6
    private var window: NSPanel?
    private var hideTask: Task<Void, Never>?

    /// Centred under Maxie (above him when he sits at the bottom edge), kept inside the screen.
    static func frame(size: CGSize, under bot: CGRect, in visible: CGRect) -> CGRect {
        let x = min(max(bot.midX - size.width / 2, visible.minX + gap), visible.maxX - size.width - gap)
        let below = bot.minY - gap - size.height
        return CGRect(x: x, y: below >= visible.minY ? below : bot.maxY + gap, width: size.width, height: size.height)
    }

    func show(_ notice: VoiceNotice, under bot: CGRect, in visible: CGRect) {
        let host = NSHostingView(rootView: VoiceNoticeView(notice: notice))
        let frame = Self.frame(size: host.fittingSize, under: bot, in: visible)
        let panel = window ?? Self.makeWindow(frame)
        panel.contentView = host
        panel.setFrame(frame, display: true)
        panel.alphaValue = 1
        panel.orderFrontRegardless()
        window = panel
        hideTask?.cancel()
        hideTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(VoiceNotice.seconds))
            guard !Task.isCancelled else { return }
            self?.hide()
        }
    }

    func hide() {
        hideTask?.cancel()
        guard let window else { return }
        NSAnimationContext.runAnimationGroup({ $0.duration = 0.25; window.animator().alphaValue = 0 }) { [weak window] in
            MainActor.assumeIsolated { window?.orderOut(nil) }
        }
    }

    private static func makeWindow(_ rect: CGRect) -> NSPanel {
        let p = BotPanel(contentRect: rect, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.isFloatingPanel = true
        p.level = .statusBar
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = false
        p.hidesOnDeactivate = false
        p.ignoresMouseEvents = true
        p.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        p.isReleasedWhenClosed = false
        return p
    }
}

/// Off-screen render only (DebugSnapshot): every notice the notch can show, under a notch-black strip.
struct VoiceNoticeSheet: View {
    static let size = CGSize(width: 420, height: 330)
    static let samples = [
        VoiceNotice(kind: "didnt_catch", text: "Didn't catch that."),
        VoiceNotice(kind: "not_your_voice", text: "That didn't sound like you. Try \u{201C}Hey Maxie\u{201D}."),
        VoiceNotice(kind: "not_enrolled", text: "Say \u{201C}Hey Maxie\u{201D}, or teach me your voice in Settings."),
        VoiceNotice(kind: "mic_silent", text: "The mic sends no sound. Check Microphone in Settings."),
        VoiceNotice(kind: "voice_down", text: VoiceNotice.voiceDown),
        VoiceNotice(kind: "hub_down", text: VoiceNotice.hubDown),
    ]

    var body: some View {
        VStack(spacing: 12) {
            Rectangle().fill(.black).frame(height: 24)
            ForEach(Self.samples) { VoiceNoticeView(notice: $0) }
            Spacer(minLength: 0)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .background(LinearGradient(colors: [Color(hex: "#6C8DB5"), Color(hex: "#D9C6B0")], startPoint: .top, endPoint: .bottom))
    }
}
