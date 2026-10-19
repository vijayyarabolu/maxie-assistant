import AppKit
import SwiftUI

/// The conversation: every turn a knot, its own dot, with no line joining them (the line down
/// the side looked ugly). His words rose, right; Maxie's mist, left; no bubbles. All text selectable; hovering
/// a turn shows "Copy". While Maxie works, the steps show as knots; what needs the user's OK appears as words with
/// Allow / Deny.
struct ConversationView: View {
    let hub: HubClient
    var level: Double = 0
    /// A brief section's fix: "connect_google", "link_jobhunt", "open_connections".
    var onAction: (String) -> Void = { _ in }
    /// The first open: Maxie's hello is the first turn; "Got it" calls this.
    var welcome: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        if let welcome { WelcomeTurn(onDone: welcome) }
                        ForEach(hub.confirmations) { c in
                            if c.screen { ScreenTurn(c: c, hub: hub) } else { ConfirmTurn(c: c, hub: hub) }
                        }
                        if hub.transcript.isEmpty, let brief = hub.brief, !brief.isEmpty {
                            BriefTurn(brief: brief, onAction: onAction)
                        }
                        ForEach(hub.transcript) { line in
                            TurnRow(line: line, speaking: hub.state == .talk && line.id == hub.transcript.last?.id && line.role != "user")
                                .id(line.id)
                        }
                        if hub.state == .think { WorkingTurn(steps: hub.steps) }
                        Color.clear.frame(height: 1).id("end")
                    }
                    .padding(.vertical, 6)
                }
                .scrollIndicators(.never)
                .onChange(of: hub.transcript.last?.text) { _, _ in
                    withAnimation(Light.spring) { proxy.scrollTo("end", anchor: .bottom) }
                }
                .onChange(of: hub.state) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
            }
            ComposerView(hub: hub, level: level)
        }
    }
}

/// One turn: knot, words, and a "Copy" word on hover.
struct TurnRow: View {
    let line: TranscriptLine
    var speaking = false
    @State private var hover = false
    @State private var copied = false

    var body: some View {
        let fromUser = line.role == "user"
        HStack(alignment: .top, spacing: 10) {
            Knot(kind: fromUser ? .user : .claude, glowing: speaking).padding(.top, 4)
            if fromUser { Spacer(minLength: 36); copyWord }
            Text(line.text)
                .font(fromUser ? Light.userText : Light.text)
                .lineSpacing(Light.leading)
                .foregroundStyle(fromUser ? Light.userWords : Light.words)
                .multilineTextAlignment(fromUser ? .trailing : .leading)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: Light.measure, alignment: fromUser ? .trailing : .leading)
            if !fromUser { Spacer(minLength: 12); copyWord }
        }
        .contentShape(Rectangle())
        .onHover { hover = $0 }
        .transition(.opacity)
    }

    private var copyWord: some View {
        Button(copied ? "Copied" : "Copy") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(line.text, forType: .string)
            copied = true
            Task { try? await Task.sleep(for: .seconds(1.2)); copied = false }
        }
        .buttonStyle(.word)
        .opacity(hover || copied ? 1 : 0)
        .allowsHitTesting(hover)
        .accessibilityLabel("Copy this message")
    }
}

/// Today's brief, before anything is said: a short title ("Good morning" before noon, "Today so far"
/// after) and one tidy section per source, short lines, left aligned. A source that isn't connected says
/// so in one line with its fix: "Gmail not connected · Connect".
struct BriefTurn: View {
    let brief: BriefInfo
    var onAction: (String) -> Void = { _ in }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Knot(kind: .claude).padding(.top, 4)
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(brief.title).font(Light.userText).foregroundStyle(Light.words)
                    Text(brief.date).font(Light.small).foregroundStyle(Light.quiet)
                }
                if brief.sections.isEmpty {
                    Text(brief.text).font(Light.text).lineSpacing(Light.leading).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(brief.sections) { section in sectionView(section) }
            }
            .frame(maxWidth: Light.measure, alignment: .leading)
        }
    }

    private func sectionView(_ s: BriefInfo.Section) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(s.name).font(Light.label).foregroundStyle(Light.quiet)
            ForEach(Array(s.lines.enumerated()), id: \.offset) { _, line in
                Text(line).font(Light.text).lineSpacing(Light.leading).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !s.missing.isEmpty {
                HStack(spacing: 0) {
                    Text(s.missing + (BriefInfo.actionWord(s.action) == nil ? "" : " · ")).font(Light.text).foregroundStyle(Light.words)
                    if let word = BriefInfo.actionWord(s.action) {
                        Button(word) { onAction(s.action) }
                            .buttonStyle(.wordPrimary)
                            .padding(.leading, -6)
                            .accessibilityLabel("\(word): \(s.missing)")
                    }
                }
            }
        }
    }
}

enum PanelHints {
    static let line = "Esc to close · ⌘, Settings · drag me anywhere · ⌥Space to open"
    static let seenKey = "panelHintsSeen"
    /// Snapshots of the first open (CLAUDE_NOTCH_FIRST_RUN=1): shown without reading or writing the user's defaults.
    static let firstRunShot = ProcessInfo.processInfo.environment["CLAUDE_NOTCH_FIRST_RUN"] != nil
}

/// While Maxie works: the Thread forks into the steps so far.
struct WorkingTurn: View {
    let steps: [Step]
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Knot(kind: .thinking)
                Text("Thinking…").font(Light.text).foregroundStyle(Light.quiet)
            }
            ForEach(steps) { s in
                HStack(spacing: 8) {
                    Knot(kind: .step(ok: s.ok))
                    Text(s.name.replacingOccurrences(of: "_", with: " ") + (s.ok ? "" : ", failed"))
                        .font(Light.small).foregroundStyle(s.ok ? Light.quiet : Light.bad)
                }
                .padding(.leading, 14)
            }
        }
    }
}

/// Something waiting for the user's OK, as words.
struct ConfirmTurn: View {
    let c: Confirmation
    let hub: HubClient
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Knot(kind: .fact, glowing: true).padding(.top, 4)
            VStack(alignment: .leading, spacing: 4) {
                Text("Needs your OK").font(Light.small).foregroundStyle(Light.notice)
                Text(c.summary).font(Light.text).lineSpacing(Light.leading).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                if c.afterUntrusted {
                    Text("Asked for after reading an email or web page. Check it first.")
                        .font(Light.small).foregroundStyle(Light.bad)
                }
                HStack(spacing: 8) {
                    Button("Allow") { hub.answer(c, approve: true) }.buttonStyle(.wordPrimary)
                    Button("Deny") { hub.answer(c, approve: false) }.buttonStyle(.word)
                }
                .padding(.leading, -6)
            }
        }
    }
}
