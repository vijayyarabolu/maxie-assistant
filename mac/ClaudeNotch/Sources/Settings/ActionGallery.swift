import SwiftUI

/// Settings › Maxie: every state he can show, by its real name, under the live preview. Tap one
/// and the preview plays it; Auto play walks through them all; on the right the same state at notch size, the
/// frames the menu bar really shows. The Classic robot gets its own, shorter list.
struct ActionGallery: View {
    let classic: Bool
    @Binding var shown: BotState
    @Binding var autoPlay: Bool

    nonisolated static let defaultInterval: Double = 3
    /// Seconds per state while auto playing (tests shorten it).
    nonisolated(unsafe) static var interval = defaultInterval

    /// Maxie can do everything; the Classic robot has no wave, eyes-closed or stretch loops.
    static func states(classic: Bool) -> [BotState] {
        let all: [BotState] = [.idle, .listen, .think, .talk, .happy, .proud, .sleep, .wave, .closed, .stretch, .gentle]
        return classic ? all.filter { ![.wave, .closed, .stretch].contains($0) } : all
    }

    /// The state after `state` in `list`, wrapping around; the first one if `state` isn't in it.
    static func next(after state: BotState, in list: [BotState]) -> BotState {
        guard let i = list.firstIndex(of: state) else { return list.first ?? .idle }
        return list[(i + 1) % list.count]
    }

    /// The chip's words: the state's own label, shortened where it would crowd the row.
    static func chip(_ state: BotState) -> String {
        state == .closed ? "Eyes closed" : state.label
    }

    private var list: [BotState] { Self.states(classic: classic) }

    var body: some View {
        SunsetCard {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(classic ? "What the Classic robot can do" : "What he can do").font(.headline)
                        Spacer()
                        autoPlayButton
                    }
                    ChipFlow(spacing: 6) {
                        ForEach(list, id: \.self) { chipButton($0) }
                    }
                }
                notchPreview
            }
        }
        .task(id: autoPlay) { await play() }
    }

    private func chipButton(_ state: BotState) -> some View {
        let chosen = state == shown
        return Button { autoPlay = false; shown = state } label: {
            Text(Self.chip(state))
                .font(.system(size: 12, weight: chosen ? .semibold : .regular))
                .foregroundStyle(chosen ? Light.onThread : Light.words)
                .padding(.vertical, 4).padding(.horizontal, 10)
                .background(Capsule(style: .circular).fill(chosen ? Light.thread : .clear))  // filled and semibold, not colour alone
                .overlay(Capsule(style: .circular).strokeBorder(chosen ? Light.thread : Light.edge, lineWidth: 1))
                .contentShape(Capsule(style: .circular))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(chosen ? .isSelected : [])
        .clickTarget("action.\(state.rawValue)")
        .probe("control")
    }

    private var autoPlayButton: some View {
        Button { autoPlay.toggle() } label: {
            Label(autoPlay ? "Stop" : "Auto play", systemImage: autoPlay ? "stop.fill" : "play.fill")
                .labelStyle(.titleAndIcon)
        }
        .buttonStyle(.sunset)
        .accessibilityValue(autoPlay ? "playing" : "stopped")
        .clickTarget("action.autoplay")
        .clickValue("maxie.autoplay", autoPlay ? "on" : "off")
    }

    private var notchPreview: some View {
        VStack(spacing: 6) {
            MascotLayer(state: shown)
                .frame(width: MascotLibrary.shared.slotSize.width, height: MascotLibrary.shared.slotSize.height)
                .padding(.horizontal, 14).padding(.vertical, 6)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.black))  // the menu bar's black
                .accessibilityLabel("At notch size: \(shown.label)")
                .clickValue("maxie.notchPreview", shown.rawValue)
            Text("Notch size").font(Light.small).foregroundStyle(Light.quiet)
        }
    }

    private func play() async {
        guard autoPlay else { return }
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(Self.interval))
            guard !Task.isCancelled, autoPlay else { return }
            shown = Self.next(after: shown, in: list)
        }
    }
}

/// Chips that wrap onto as many lines as they need.
struct ChipFlow: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = rows(width: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map(\.width).max() ?? 0
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0))
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for i in row.items {
                let size = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row { var items: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func rows(width: CGFloat, subviews: Subviews) -> [Row] {
        subviews.indices.reduce(into: [Row]()) { rows, i in
            let size = subviews[i].sizeThatFits(.unspecified)
            if let last = rows.last, last.width + spacing + size.width <= width {
                rows[rows.count - 1] = Row(items: last.items + [i], width: last.width + spacing + size.width,
                                           height: max(last.height, size.height))
            } else {
                rows.append(Row(items: [i], width: size.width, height: size.height))
            }
        }
    }
}
