import Foundation

/// the voice helper's notices and heartbeat. `notice` is what the notch shows under Maxie for a few seconds;
/// `voiceAlive` false (no poll for 5 s) shows "Voice service stopped" and the app restarts the helper.
extension HubClient {
    struct VoiceStatus: Decodable { let alive: Bool; let seen_s_ago: Double?; let mic: String; let muted: Bool }

    func show(_ new: VoiceNotice) {
        notice = new
        noticeTask?.cancel()
        noticeTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(VoiceNotice.seconds))
            if !Task.isCancelled, self?.notice?.id == new.id { self?.notice = nil }
        }
    }

    func setVoiceAlive(_ alive: Bool) {
        let wasAlive = voiceAlive
        voiceAlive = alive
        if !wasAlive, alive { onVoiceUp?() }
        guard wasAlive, !alive else { return }
        show(VoiceNotice(kind: "voice_down", text: VoiceNotice.voiceDown))
        onVoiceDown?()
    }

    /// On (re)connect: the helper may have stopped while the stream was down. A hub that never heard from it yet
    /// (it just restarted) knows nothing: that is not "stopped".
    func refreshVoice() async {
        guard let st: VoiceStatus = await fetch("/api/voice/status"), st.seen_s_ago != nil else { return }
        setVoiceAlive(st.alive)
    }
}
