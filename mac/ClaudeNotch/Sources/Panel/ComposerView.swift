import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// What you attach to a message: a pasted or dropped image (as a data URL) or a dropped file (path).
struct Attachment: Identifiable, Equatable {
    let id = UUID()
    let name: String
    var image: String? = nil
    var path: String? = nil
}

/// The input: one line, no box. The mic's live waveform on its left while you talk, a breathing ember caret,
/// mic and look (an eye) as 20 pt glyphs that light up on hover, Return sends (no send button). The hint about
/// Return / Shift-Return shows until the first message, then never again. "+", Cmd-V and drag-and-drop attach
/// images and files.
struct ComposerView: View {
    let hub: HubClient
    var level: Double = 0
    @State private var text = ""
    @State private var attachments: [Attachment] = []
    @FocusState private var focused: Bool
    @AppStorage("composerHintSeen") private var hintSeen = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !attachments.isEmpty {
                HStack(spacing: 10) {
                    ForEach(attachments) { a in
                        Button("\(a.name), remove") { attachments.removeAll { $0.id == a.id } }
                            .buttonStyle(.word).font(Light.small)
                    }
                }
            }
            HStack(alignment: .center, spacing: 10) {
                GlyphButton(symbol: "plus", label: "Attach an image or a file") { pick() }
                if Self.showsWave(hub.state) {
                    MicWave(active: true, level: level)
                        .frame(width: 26, height: 18)
                        .transition(.opacity)
                }
                TextField("", text: $text, prompt: Text("Ask or just talk").foregroundStyle(Light.quiet), axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(Light.text)
                    .foregroundStyle(Light.words)
                    .lineLimit(1...5)
                    .focused($focused)
                    .onKeyPress(.return, phases: .down) { press in
                        if press.modifiers.contains(.shift) {
                            text += "\n"
                            return .handled
                        }
                        send()
                        return .handled
                    }
                GlyphButton(symbol: "mic", label: "Listen now, like saying \"Hey Max\"") { hub.wakeVoice() }
                GlyphButton(symbol: Self.lookSymbol, label: "Look at my screen (\(HotKeys.optionShiftS.label))") {
                    hub.lookAtScreen(question: text.isEmpty ? "What's on my screen?" : text)
                    text = ""
                }
            }
            .padding(.vertical, 6)
            .animation(.easeOut(duration: 0.2), value: Self.showsWave(hub.state))
            if !hintSeen {
                Text("Return sends. Shift-Return adds a line.").font(Light.small).foregroundStyle(Light.quiet)
                    .transition(.opacity)
            }
            if hub.sending || hub.queued > 0 {
                Text(hub.queued > 0 ? "Sending, \(hub.queued) waiting" : "Sending…").font(Light.small).foregroundStyle(Light.quiet)
            }
            if !hub.lastError.isEmpty {
                Text(hub.lastError).font(Light.small).foregroundStyle(Light.bad)
            }
        }
        .onAppear { focused = true }
        .onPasteCommand(of: [.png, .jpeg, .tiff, .image, .fileURL]) { _ in pasteFromClipboard() }
        .onDrop(of: [.fileURL, .image], isTargeted: nil) { providers in
            drop(providers)
            return true
        }
    }

    /// At rest the wave was a row of grey dots before "Ask or just talk": it shows only while you talk.
    static func showsWave(_ state: BotState) -> Bool { state == .listen }

    /// "Look at my screen" is an eye, like the watching eye by his name (it was a dashed square).
    static let lookSymbol = "eye"

    private func send() {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty || !attachments.isEmpty else { return }
        hub.send(text: t, images: attachments.compactMap(\.image), files: attachments.compactMap(\.path))
        text = ""
        attachments = []
        if !hintSeen { withAnimation(.easeOut(duration: 1.2)) { hintSeen = true } }
    }

    /// "+": pick files to attach. Maxie comes forward so the picker shows over your work.
    private func pick() {
        let panel = Self.picker()
        NSApp.activate()
        panel.begin { response in
            guard response == .OK else { return }
            MainActor.assumeIsolated { panel.urls.forEach(add(file:)) }
        }
    }

    static func picker() -> NSOpenPanel {
        let p = NSOpenPanel()
        p.canChooseFiles = true
        p.canChooseDirectories = false
        p.allowsMultipleSelection = true
        p.prompt = "Attach"
        p.message = "Attach to your message to Maxie"
        return p
    }

    private func pasteFromClipboard() {
        let pb = NSPasteboard.general
        if let urls = pb.readObjects(forClasses: [NSURL.self]) as? [URL], !urls.isEmpty {
            urls.forEach(add(file:))
            return
        }
        if let image = NSImage(pasteboard: pb), let url = ImageEncoding.jpegDataURL(image, maxSide: ScreenCapture.maxSide) {
            attachments.append(Attachment(name: "pasted image", image: url))
        }
    }

    private func add(file url: URL) { attachments.append(Self.attachment(for: url)) }

    /// The picker, a paste and a drop attach alike: an image as a picture Maxie can see, anything else by its path.
    static func attachment(for url: URL) -> Attachment {
        let ext = url.pathExtension.lowercased()
        if ["png", "jpg", "jpeg", "gif", "webp", "tiff", "heic"].contains(ext), let image = NSImage(contentsOf: url),
           let data = ImageEncoding.jpegDataURL(image, maxSide: ScreenCapture.maxSide) {
            return Attachment(name: url.lastPathComponent, image: data)
        }
        return Attachment(name: url.lastPathComponent, path: url.path)
    }

    private func drop(_ providers: [NSItemProvider]) {
        for p in providers {
            if p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                p.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { data, _ in
                    guard let data, let url = URL(dataRepresentation: data, relativeTo: nil) else { return }
                    Task { @MainActor in add(file: url) }
                }
            } else if p.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                p.loadDataRepresentation(forTypeIdentifier: UTType.image.identifier) { data, _ in
                    guard let data, let image = NSImage(data: data),
                          let url = ImageEncoding.jpegDataURL(image, maxSide: ScreenCapture.maxSide) else { return }
                    Task { @MainActor in attachments.append(Attachment(name: "dropped image", image: url)) }
                }
            }
        }
    }
}
