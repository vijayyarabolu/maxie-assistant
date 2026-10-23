import Foundation

/// Pause Maxie. One switch on the hub (`/api/pause`) for the menu, ⌥⇧P, a click on him and
/// "Maxie, go to sleep": paused, he stops listening, watching and his own work, and his eyes close.
extension HubClient {
    struct PauseInfo: Decodable { let paused: Bool }

    func syncPause() async {
        if let p: PauseInfo = await fetch("/api/pause") { paused = p.paused }
    }

    func setPaused(_ on: Bool) {
        paused = on
        Task {
            let (code, body) = await postJSON("/api/pause", dict: ["on": on])
            guard code == 200, let p = try? JSONDecoder().decode(PauseInfo.self, from: Data(body.utf8)) else {
                paused = !on
                show(VoiceNotice(kind: "hub_down", text: VoiceNotice.hubDown))
                return
            }
            paused = p.paused
            await syncMuted()
        }
    }

    func togglePause() { setPaused(!paused) }
}
