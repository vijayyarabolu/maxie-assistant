import AppKit
import SwiftUI

enum PanelTab: String, CaseIterable, Identifiable {
    case chat = "Chat", activity = "Activity", memory = "Memory"
    var id: String { rawValue }
}

/// What the notch opens into ("light, not windows"): Maxie, its name, three words, and the
/// conversation floating on light that fades to nothing at the edges. No frame, no cards, no bubbles.
struct PanelView: View {
    let hub: HubClient
    let onClose: () -> Void
    var onSettings: (SettingsSection?) -> Void = { _ in }
    var onPin: (Bool) -> Void = { _ in }
    var onQuit: () -> Void = {}
    var onResize: (CGFloat) -> Void = { _ in }
    var onResizeEnd: () -> Void = {}
    /// Where the light comes from: Maxie's position across the top of the panel.
    var source: UnitPoint = .top
    @State private var tab: PanelTab = PanelView.initialTab
    @State private var meter = MicMeter()
    @State private var hovering = false
    @State private var linkNote = ""
    @AppStorage(PanelView.pinnedKey) private var pinned = false
    @AppStorage(PanelHints.seenKey) private var hintsSeen = false
    @Namespace private var tabLine

    static let pinnedKey = "panelPinned"
    private var firstRun: Bool { PanelHints.firstRunShot || !hintsSeen }
    /// Snapshots show the hover controls without a pointer over the panel.
    static let showControlsAlways = ProcessInfo.processInfo.environment["CLAUDE_NOTCH_SNAPSHOT"] != nil

    /// CLAUDE_NOTCH_TAB=activity|memory opens the panel on that tab (used by DebugSnapshot).
    static var initialTab: PanelTab {
        let want = (ProcessInfo.processInfo.environment["CLAUDE_NOTCH_TAB"] ?? "").lowercased()
        return PanelTab.allCases.first { $0.rawValue.lowercased() == want } ?? .chat
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // First open (WelcomeTurn, the first turn of the chat, is the onboarding): Maxie waves hello.
            LiveBotView(base: hub.base, state: !firstRun || hub.state != .idle ? hub.state : .wave,
                        character: hub.maxie?.character ?? MaxieLook.character().rawValue, look: hub.maxie?.look ?? [:])
                .frame(width: 84, height: 124)
                .frame(maxWidth: .infinity)
                .padding(.top, 6)
                .overlay { WindowDragArea().help("Drag to move") }  // the top of the panel moves it
            nameRow
            attention
            if let s = hub.suggestion { SuggestionCard(suggestion: s, hub: hub).padding(.top, 10) }
            if !linkNote.isEmpty { Text(linkNote).font(Light.small).foregroundStyle(Light.quiet).padding(.top, 4) }
            if let steps = hub.pendingFlow { NameFlowView(hub: hub, steps: steps) }
            words
            Group {
                switch tab {
                case .chat: ConversationView(hub: hub, level: meter.level, onAction: briefAction,
                                             welcome: firstRun ? { withAnimation(Light.spring) { hintsSeen = true } } : nil)
                case .activity: ActivityTab(hub: hub)
                case .memory: MemoryTab(hub: hub)
                }
            }
            .textSelection(.enabled)  // your words, Maxie's replies, notes: all selectable, ⌘C copies
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .padding(.horizontal, Light.gutter + 6)
        .padding(.bottom, 14)
        .frame(width: 420)
        .frame(maxHeight: .infinity, alignment: .top)
        .overlay(alignment: .topTrailing) { controls }
        .overlay(alignment: .bottom) { ResizeEdge(onResize: onResize, onEnd: onResizeEnd) }
        .background(LightField(source: source))
        .foregroundStyle(Light.words)
        .tint(Light.thread)
        .onHover { inside in withAnimation(.easeOut(duration: 0.15)) { hovering = inside } }
        .onChange(of: hub.state, initial: true) { _, s in meter.run(s == .listen) }
        .onDisappear { meter.run(false) }
    }

    /// Top right, on hover: Settings, Pin (keep open), Close.
    private var controls: some View {
        HStack(spacing: 6) {
            PanelGlyph(symbol: "gearshape", label: "Settings (⌘,)") { onSettings(nil) }
            PanelGlyph(symbol: pinned ? "pin.fill" : "pin", label: pinned ? "Unpin: close when I click elsewhere" : "Pin: keep the panel open",
                       on: pinned) {
                pinned.toggle()
                onPin(pinned)
            }
            PanelGlyph(symbol: "xmark", label: "Close (Esc)", action: onClose)
        }
        .padding(.top, 12).padding(.trailing, 14)
        .opacity(hovering || pinned || Self.showControlsAlways ? 1 : 0)
        .allowsHitTesting(hovering || pinned || Self.showControlsAlways)
    }

    private func briefAction(_ action: String) {
        switch action {
        case "link_jobhunt":
            guard Self.confirmLinkJobhunt() else { return }
            linkNote = "Linking jobhunt…"
            Task { linkNote = await hub.linkJobhunt() }
        default: onSettings(.connections)
        }
    }

    /// The user's OK before Maxie reads the jobhunt and resumegen token files.
    static func confirmLinkJobhunt() -> Bool {
        MacHelpers.confirm("Link jobhunt?", "Maxie reads the jobhunt and resumegen token files set in Settings and keeps them on this Mac, in your Keychain. Neither app is changed.", ok: "Link")
    }

    private var nameRow: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Maxie").font(Light.name).tracking(-0.5)
            EyeToggle(hub: hub).padding(.leading, 8)  // watching or not, always in sight
            Spacer()
            Text(status).font(Light.small).foregroundStyle(hub.connected ? Light.quiet : Light.bad)
                .contentTransition(.opacity)
                .animation(Light.spring, value: status)
            if let spend = monthSpend {
                Text(spend).font(Light.small).foregroundStyle(Light.quiet).padding(.leading, 10)
            }
        }
        .padding(.top, 2)
        .background(WindowDragArea())
    }

    /// Only what needs the user: a stale backup, an upgraded model. Words, no chips.
    @ViewBuilder private var attention: some View {
        if hub.backupStale, !hub.backupLine.isEmpty {
            Text(hub.backupLine).font(Light.small).foregroundStyle(Light.bad).padding(.top, 2)
        }
        ForEach(hub.modelLines, id: \.self) { Text($0).font(Light.small).foregroundStyle(Light.quiet).padding(.top, 2) }
    }

    private var words: some View {
        HStack(spacing: 14) {
            ForEach(PanelTab.allCases) { t in
                Button(t.rawValue) { withAnimation(Light.spring) { tab = t } }
                    .buttonStyle(.plain)
                    .font(.system(size: 13, weight: tab == t ? .medium : .regular))
                    .foregroundStyle(tab == t ? Light.words : Light.faint)
                    .overlay(alignment: .bottom) {
                        if tab == t {  // the tab you are on: an ember line that slides to the next one
                            Capsule().fill(Light.thread).frame(height: 2).offset(y: 6)
                                .matchedGeometryEffect(id: "tab", in: tabLine)
                        }
                    }
                    .contentShape(Rectangle().inset(by: -4))
                    .accessibilityAddTraits(tab == t ? .isSelected : [])
            }
            Spacer()
            Menu("More") {
                Button("Settings…") { onSettings(nil) }
                Button("Copy conversation") { copyConversation() }.disabled(hub.transcript.isEmpty)
                Button("Clear chat") { hub.clearChat() }.disabled(hub.transcript.isEmpty)
                Button(hub.voiceMuted ? "Resume listening" : "Pause listening") { hub.setMuted(!hub.voiceMuted) }
                Button(hub.watching ? "Stop recording" : "Watch me once") { hub.onToggleWatch?() }
                Divider()
                Button("Quit Maxie…", action: onQuit)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .tint(Light.faint)
            .fixedSize()
            .font(.system(size: 13))
            .foregroundStyle(Light.faint)
        }
        .padding(.top, 8).padding(.bottom, 10)
    }

    private var status: String {
        guard hub.connected else { return "Offline" }
        if hub.watching { return "Watching you" }
        if hub.state == .think, let step = hub.steps.last?.name {
            return "Thinking: " + step.replacingOccurrences(of: "_", with: " ")
        }
        return hub.state.label
    }

    /// "$0.13 this month" from the hub's budget line.
    private var monthSpend: String? {
        let line = hub.budgetLine
        guard let r = line.range(of: #"\$[0-9.]+ of \$[0-9.]+ this month"#, options: .regularExpression) else { return nil }
        let used = line[r].split(separator: " ").first.map(String.init) ?? ""
        return used.isEmpty ? nil : "\(used) this month"
    }

    /// The conversation as plain text: "You: …" / "Maxie: …", a blank line between turns.
    static func conversationText(_ lines: [TranscriptLine]) -> String {
        lines.map { ($0.role == "user" ? "You: " : "Maxie: ") + $0.text }.joined(separator: "\n\n")
    }

    private func copyConversation() {
        let text = Self.conversationText(hub.transcript)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}

/// A 14 pt glyph in the panel's corner: quiet, ember on hover (or when on). Always has a spoken label.
struct PanelGlyph: View {
    let symbol: String
    let label: String
    var on = false
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .medium))
                .frame(width: 22, height: 22)
                .foregroundStyle(hover || on ? Light.thread : Light.words.opacity(0.7))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(label)
        .accessibilityLabel(label)
    }
}

/// Drag here to move the window (the panel's top: Maxie and its name).
struct WindowDragArea: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { Handle() }
    func updateNSView(_ nsView: NSView, context: Context) {}

    final class Handle: NSView {
        override var mouseDownCanMoveWindow: Bool { true }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
        override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }
    }
}

/// The bottom edge: drag up or down to make the panel shorter or taller (the top stays put).
struct ResizeEdge: NSViewRepresentable {
    var onResize: (CGFloat) -> Void
    var onEnd: () -> Void

    func makeNSView(context: Context) -> Edge {
        let e = Edge()
        e.onResize = onResize
        e.onEnd = onEnd
        return e
    }

    func updateNSView(_ e: Edge, context: Context) {
        e.onResize = onResize
        e.onEnd = onEnd
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: Edge, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 420, height: 8)
    }

    final class Edge: NSView {
        var onResize: (CGFloat) -> Void = { _ in }
        var onEnd: () -> Void = {}
        private var start: (mouse: CGFloat, height: CGFloat)?

        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func resetCursorRects() { addCursorRect(bounds, cursor: .resizeUpDown) }
        override func mouseDown(with event: NSEvent) { start = (NSEvent.mouseLocation.y, window?.frame.height ?? 0) }
        override func mouseDragged(with event: NSEvent) {
            guard let start else { return }
            onResize(start.height + (start.mouse - NSEvent.mouseLocation.y))
        }
        override func mouseUp(with event: NSEvent) {
            if start != nil { onEnd() }
            start = nil
        }
    }
}
