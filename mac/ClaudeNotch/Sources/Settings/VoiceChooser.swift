import SwiftUI

/// Settings › Maxie › Voice: the voice he has, with ▶︎ to hear it, and a list where resting on a voice (pointer or
/// keyboard focus) plays "Hi there, I'm Maxie." in it; clicking one picks it.
struct VoiceChooser: View {
    let voices: [MaxieInfo.Voice]
    @Binding var voice: String
    let speed: Double
    let player: VoicePreviewPlayer
    @State private var open = false

    private var label: String { voices.first { $0.id == voice }?.label ?? voice }

    var body: some View {
        HStack(spacing: 6) {
            Button { open.toggle() } label: {
                HStack(spacing: 4) {
                    Text(label).lineLimit(1)
                    Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .semibold))
                }
            }
            .buttonStyle(.sunset)
            .clickTarget("voice.menu")
            .popover(isPresented: $open, arrowEdge: .bottom) {
                VoiceList(voices: voices, voice: $voice, speed: speed, player: player) { open = false }
            }
            PlayButton(voice: voice, speed: speed, player: player).clickTarget("voice.play")
        }
    }
}

/// ▶︎ plays the sample, ■ stops it; a spinner while the first one is made.
struct PlayButton: View {
    let voice: String
    let speed: Double
    let player: VoicePreviewPlayer

    var body: some View {
        let busy = player.loading == voice, on = player.playing == voice
        Button { player.toggle(voice, speed: speed) } label: {
            Group {
                if busy { ProgressView().controlSize(.small) } else { Image(systemName: on ? "stop.fill" : "play.fill") }
            }
            .frame(width: 18, height: 18)
        }
        .buttonStyle(.sunset)
        .help(on ? "Stop" : "Hear this voice")
        .accessibilityLabel(on ? "Stop the sample" : "Hear this voice")
    }
}

struct VoiceList: View {
    let voices: [MaxieInfo.Voice]
    @Binding var voice: String
    let speed: Double
    let player: VoicePreviewPlayer
    var onPick: () -> Void
    @FocusState private var focused: String?
    @State private var hovered: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(voices) { v in
                    Button { voice = v.id; onPick() } label: {
                        HStack {
                            Image(systemName: "checkmark").opacity(v.id == voice ? 1 : 0).font(.system(size: 11, weight: .bold))
                            Text(v.label)
                            Spacer()
                            if player.playing == v.id { Image(systemName: "speaker.wave.2.fill").font(.system(size: 11)) }
                        }
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .background(RoundedRectangle(cornerRadius: 5).fill(hovered == v.id || focused == v.id ? Light.thread.opacity(0.22) : .clear))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .focused($focused, equals: v.id)
                    .onHover { inside in if inside { hovered = v.id; rest(on: v.id) } else if hovered == v.id { hovered = nil } }
                    .accessibilityAddTraits(v.id == voice ? .isSelected : [])
                }
            }
            .padding(6)
        }
        .frame(width: 240, height: 320)
        .onChange(of: focused) { _, id in if let id { rest(on: id) } }
        .onDisappear { player.stop() }
    }

    /// Resting on a voice for a moment plays it; sweeping across the list doesn't.
    private func rest(on id: String) {
        Task {
            try? await Task.sleep(for: .milliseconds(280))
            if hovered == id || focused == id { player.play(id, speed: speed) }
        }
    }
}
