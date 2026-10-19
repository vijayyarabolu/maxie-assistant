import Foundation

/// Settings › Maxie as the hub describes it (GET/PUT /api/maxie): his character and look, how Maxie
/// behaves and sounds, the state of the notch-loop re-render, and every option the pickers offer.
struct MaxieInfo: Decodable, Equatable {
    struct Fur: Decodable, Equatable, Identifiable { let id: String; let name: String; let color: String }
    struct Voice: Decodable, Equatable, Identifiable { let id: String; let label: String }
    struct Options: Decodable, Equatable {
        let fur: [Fur]
        let furLength, ears, eyes, accessory, maxie_size, personality, chattiness: [String]
        let voice: [Voice]
        let voice_speed: [Double]
    }

    let character: String
    let look: [String: String]
    let render: String          // bundled | ready | rendering | failed | none
    let loops_dir: String?
    let maxie_size: String
    let personality: String
    let chattiness: String
    let voice: String
    let voice_speed: Double
    let quiet_start: String
    let quiet_end: String
    let sleep_mode: String?  // "presence": sleeps when the user sleeps; "fixed": quiet hours
    let brief_sleep: Bool?
    let options: Options

    var isClassic: Bool { character == "classic" }

    /// One line under the preview: where the notch bot's frames come from right now.
    var renderLine: String {
        if isClassic { return "The Classic robot, built in." }
        switch render {
        case "bundled": return "His pick, built in: Mochi, no headphones."
        case "ready": return "Your look is on the notch."
        case "rendering": return "Drawing your look for the notch, in the background. The current one stays until it's done."
        case "failed": return "Couldn't draw that look for the notch. The last one stays."
        default: return "Not drawn for the notch yet."
        }
    }
}

extension HubClient {
    /// Reads Settings › Maxie and puts the right bot on screen (character, custom loops, size).
    func syncMaxie() async {
        if let m: MaxieInfo = await fetch("/api/maxie") { applyMaxie(m) }
    }

    /// Changes Settings › Maxie; returns the hub's error message, or nil when it took.
    @discardableResult
    func putMaxie(_ changes: [String: Any]) async -> String? {
        if let sink = maxieSink { sink(changes); return nil }  // click tests record the save instead of sending it
        let (code, body) = await put("/api/maxie", dict: changes)
        guard code == 200, let m = try? JSONDecoder().decode(MaxieInfo.self, from: Data(body.utf8)) else {
            return code == 0 ? "The hub isn't answering." : Self.detail(body)
        }
        applyMaxie(m)
        return nil
    }

    func applyMaxie(_ m: MaxieInfo) {
        maxie = m
        guard DebugSnapshot.dir == nil else { return }  // a snapshot run talks to the fake hub: never store its look
        if MaxieLook.apply(character: m.character, loopsDir: m.loops_dir, size: m.maxie_size) {
            MascotLibrary.shared.reset()  // the notch bot reloads (and re-sizes) with the new frames
        }
    }

    static func detail(_ body: String) -> String {
        struct Err: Decodable { let detail: String }
        return (try? JSONDecoder().decode(Err.self, from: Data(body.utf8)).detail) ?? "That didn't save."
    }
}
