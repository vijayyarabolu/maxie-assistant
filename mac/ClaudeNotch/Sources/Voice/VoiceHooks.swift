import Foundation

/// Voice on the Mac plugs in here. Stubs only: nothing listens yet.
///
/// TODO(mac, P2 voice):
/// - Wake word: openWakeWord "Hey Max" model (trained on your own recordings) running in a local
///   helper process; call `onWake` when it fires. Nothing leaves the Mac before the wake word.
/// - Mic + VAD, streaming STT (measure whisper.cpp vs faster-whisper vs Apple Speech; pick the fastest),
///   POST the text to the hub's /api/chat and speak the streamed reply with Kokoro (Apple voices as fallback).
/// - Follow-up window ~8 s without the wake word; barge-in stops speech; "Max, stop" -> hub /api/stop.
/// - Lid closed: the built-in mic is off; only AirPods / external mics work.
@MainActor
final class VoiceHooks {
    var onWake: (() -> Void)?

    func start() {
        // TODO(mac): start the wake-word helper and wire its detections to `onWake`.
    }

    func stop() {
        // TODO(mac): stop listening (mute from the menu, quiet hours).
    }
}
