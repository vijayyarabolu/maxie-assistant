import Carbon
import Foundation

/// One step of a flow watched once (the hub's maxie/flows.py checks the same shape).
/// open_app: bring an app forward. click: press the named control. type: type text. key: one key with modifiers.
struct FlowStep: Codable, Equatable, Sendable {
    enum Kind: String, Codable, Sendable { case openApp = "open_app", click, type, key }

    var kind: Kind
    var bundle = "", name = ""                                              // open_app
    var app = "", window = "", role = "", title = "", label = "", id = ""   // click
    var text = ""                                                           // type
    var key = "", mods: [String] = []                                       // key

    enum CodingKeys: String, CodingKey { case kind = "do", bundle, name, app, window, role, title, label, id, text, key, mods }

    init(kind: Kind) { self.kind = kind }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(Kind.self, forKey: .kind)
        func s(_ k: CodingKeys) -> String { (try? c.decodeIfPresent(String.self, forKey: k)) ?? "" }
        bundle = s(.bundle); name = s(.name); app = s(.app); window = s(.window); role = s(.role)
        title = s(.title); label = s(.label); id = s(.id); text = s(.text); key = s(.key)
        mods = (try? c.decodeIfPresent([String].self, forKey: .mods)) ?? []
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(kind, forKey: .kind)
        switch kind {
        case .openApp:
            try c.encode(bundle, forKey: .bundle); try c.encode(name, forKey: .name)
        case .click:
            for (k, v) in [(CodingKeys.app, app), (.window, window), (.role, role), (.title, title), (.label, label), (.id, id)] {
                try c.encode(v, forKey: k)
            }
        case .type:
            try c.encode(text, forKey: .text)
        case .key:
            try c.encode(key, forKey: .key); try c.encode(mods, forKey: .mods)
        }
    }

    static func openApp(bundle: String, name: String) -> FlowStep {
        var s = FlowStep(kind: .openApp); s.bundle = bundle; s.name = name; return s
    }
    static func typing(_ text: String) -> FlowStep {
        var s = FlowStep(kind: .type); s.text = text; return s
    }
    static func press(_ key: String, mods: [String] = []) -> FlowStep {
        var s = FlowStep(kind: .key); s.key = key; s.mods = mods; return s
    }

    /// For the hub's JSON body.
    var json: [String: Any] {
        guard let data = try? JSONEncoder().encode(self),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }
}

/// Key names shared by the recorder and the player (the hub accepts the same names).
enum FlowKeys {
    static let codes: [String: Int] = [
        "return": kVK_Return, "tab": kVK_Tab, "escape": kVK_Escape, "space": kVK_Space, "delete": kVK_Delete,
        "up": kVK_UpArrow, "down": kVK_DownArrow, "left": kVK_LeftArrow, "right": kVK_RightArrow,
        "pageup": kVK_PageUp, "pagedown": kVK_PageDown, "home": kVK_Home, "end": kVK_End,
        "a": kVK_ANSI_A, "b": kVK_ANSI_B, "c": kVK_ANSI_C, "d": kVK_ANSI_D, "e": kVK_ANSI_E, "f": kVK_ANSI_F,
        "g": kVK_ANSI_G, "h": kVK_ANSI_H, "i": kVK_ANSI_I, "j": kVK_ANSI_J, "k": kVK_ANSI_K, "l": kVK_ANSI_L,
        "m": kVK_ANSI_M, "n": kVK_ANSI_N, "o": kVK_ANSI_O, "p": kVK_ANSI_P, "q": kVK_ANSI_Q, "r": kVK_ANSI_R,
        "s": kVK_ANSI_S, "t": kVK_ANSI_T, "u": kVK_ANSI_U, "v": kVK_ANSI_V, "w": kVK_ANSI_W, "x": kVK_ANSI_X,
        "y": kVK_ANSI_Y, "z": kVK_ANSI_Z, "0": kVK_ANSI_0, "1": kVK_ANSI_1, "2": kVK_ANSI_2, "3": kVK_ANSI_3,
        "4": kVK_ANSI_4, "5": kVK_ANSI_5, "6": kVK_ANSI_6, "7": kVK_ANSI_7, "8": kVK_ANSI_8, "9": kVK_ANSI_9,
    ]
    static let names: [Int: String] = Dictionary(uniqueKeysWithValues: codes.map { ($1, $0) })
    /// Keys that are a step of their own even without a modifier (everything else is typing).
    static let special: Set<String> = ["return", "tab", "escape", "up", "down", "left", "right", "pageup", "pagedown", "home", "end"]
}
