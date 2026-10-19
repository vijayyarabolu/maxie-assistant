import AppKit

/// Watch me once: ⌥⇧W, "watch me" or the panel's More menu starts watching; the same again stops
/// it and the panel asks for a name. A replay comes from the hub only after the user approved it there.
@MainActor
final class FlowCoordinator {
    private let hub: HubClient
    private let notch: NotchController
    private let recorder = FlowRecorder()
    private let player = FlowPlayer()
    private var playing: Task<Void, Never>?

    init(hub: HubClient, notch: NotchController) {
        self.hub = hub
        self.notch = notch
        hub.onFlowWatch = { [weak self] on in on ? self?.startWatching() : self?.stopWatching() }
        hub.onFlowRun = { [weak self] run, name, steps in self?.replay(run: run, name: name, steps: steps) }
        hub.onToggleWatch = { [weak self] in self?.toggle() }
    }

    func toggle() {
        recorder.isRecording ? stopWatching() : startWatching()
    }

    func startWatching() {
        guard !recorder.isRecording else { return }
        guard recorder.start() else {
            hub.note("Turn on Accessibility for Maxie in System Settings › Privacy & Security, then try again.")
            notch.expand(reason: .menu)
            return
        }
        hub.watching = true
        hub.pendingFlow = nil
        hub.note("Watching. Do it once, then press \(HotKeys.optionShiftW.label) or say done watching.")
    }

    func stopWatching() {
        guard recorder.isRecording else { return }
        hub.watching = false
        Task { await finishWatching(await recorder.stop()) }
    }

    private func finishWatching(_ steps: [FlowStep]) {
        var notes: [String] = []
        if recorder.skippedSecret { notes.append("I skipped what you typed into a password field.") }
        if recorder.unnamedClicks > 0 { notes.append("\(recorder.unnamedClicks) click\(recorder.unnamedClicks == 1 ? " was" : "s were") on nothing with a name, so I left \(recorder.unnamedClicks == 1 ? "it" : "them") out.") }
        if steps.filter({ $0.kind != .openApp }).isEmpty {
            hub.note((["I didn't see any steps I could repeat."] + notes).joined(separator: " "))
        } else {
            hub.pendingFlow = steps
            if !notes.isEmpty { hub.note(notes.joined(separator: " ")) }
        }
        notch.expand(reason: .menu)
    }

    private func replay(run: String, name: String, steps: [FlowStep]) {
        playing?.cancel()
        playing = Task { [weak self] in
            guard let self else { return }
            self.hub.note("Replaying \(name).")
            let out = await self.player.play(steps)
            await self.hub.postJSON("/api/flows/runs/\(run)", dict: ["ok": out.ok, "done": out.done, "error": out.error])
        }
    }
}
