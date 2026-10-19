import AppKit

/// Replays a flow: brings each app forward, finds each control again by what it is called, presses it,
/// types, and sends keys. Stops at the first step it can't do and says which.
@MainActor
final class FlowPlayer {
    struct Outcome: Sendable { let ok: Bool; let done: Int; let error: String }

    static let pause: Duration = .milliseconds(350)
    static let findFor: Duration = .seconds(8)  // apps can be slow to answer Accessibility right after a click
    static let openFor: Duration = .seconds(5)
    private var pid: pid_t?

    func play(_ steps: [FlowStep]) async -> Outcome {
        guard AXNode.trusted(prompt: true) else {
            return Outcome(ok: false, done: 0, error: "Accessibility is off for Maxie (System Settings › Privacy & Security › Accessibility)")
        }
        pid = NSWorkspace.shared.frontmostApplication?.processIdentifier
        AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), 1.0)
        for (i, s) in steps.enumerated() {
            if Task.isCancelled { return Outcome(ok: false, done: i, error: "stopped") }
            if let problem = await run(s) { return Outcome(ok: false, done: i, error: problem) }
            try? await Task.sleep(for: Self.pause)
        }
        return Outcome(ok: true, done: steps.count, error: "")
    }

    /// nil when the step worked, else what went wrong (for the user).
    private func run(_ s: FlowStep) async -> String? {
        switch s.kind {
        case .openApp: return await open(s)
        case .click: return await click(s)
        case .type: return type(s.text)
        case .key: return press(s.key, mods: s.mods)
        }
    }

    private func open(_ s: FlowStep) async -> String? {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: s.bundle) else {
            return "\(s.name) isn't installed"
        }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        guard let app = try? await NSWorkspace.shared.openApplication(at: url, configuration: config) else {
            return "couldn't open \(s.name)"
        }
        pid = app.processIdentifier
        let clock = ContinuousClock(), end = clock.now + Self.openFor
        while clock.now < end, NSWorkspace.shared.frontmostApplication?.processIdentifier != app.processIdentifier {
            app.activate()
            try? await Task.sleep(for: .milliseconds(100))
        }
        return nil
    }

    private func click(_ s: FlowStep) async -> String? {
        let what = [s.title, s.label, s.id].first { !$0.isEmpty } ?? "the control"
        guard let found = await find(s) else { return "couldn't find \(what)" }
        if found.press() { return nil }
        guard let f = found.frame else { return "couldn't press \(what)" }
        let p = CGPoint(x: f.midX, y: f.midY)  // Accessibility and CGEvent share top-left screen coordinates
        for type in [CGEventType.leftMouseDown, .leftMouseUp] {
            CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
        }
        return nil
    }

    private func find(_ s: FlowStep) async -> AXNode? {
        let clock = ContinuousClock(), end = clock.now + Self.findFor
        while clock.now < end {
            let owner = NSRunningApplication.runningApplications(withBundleIdentifier: s.app).first?.processIdentifier ?? pid
            if let owner {
                let app = AXNode.app(owner)
                let windows = app.windows.filter { !s.window.isEmpty && $0.title == s.window }
                for root in windows + [app] {
                    if let hit = FlowMatcher.find(in: root, step: s) { return hit }
                }
            }
            try? await Task.sleep(for: .milliseconds(250))
        }
        return nil
    }

    private func type(_ text: String) -> String? {
        for ch in text {
            let units = Array(String(ch).utf16)
            for down in [true, false] {
                guard let e = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down) else { return "couldn't type" }
                e.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                e.post(tap: .cghidEventTap)
            }
        }
        return nil
    }

    private func press(_ key: String, mods: [String]) -> String? {
        guard let code = FlowKeys.codes[key] else { return "unknown key \(key)" }
        var flags: CGEventFlags = []
        if mods.contains("cmd") { flags.insert(.maskCommand) }
        if mods.contains("shift") { flags.insert(.maskShift) }
        if mods.contains("option") { flags.insert(.maskAlternate) }
        if mods.contains("control") { flags.insert(.maskControl) }
        for down in [true, false] {
            guard let e = CGEvent(keyboardEventSource: nil, virtualKey: CGKeyCode(code), keyDown: down) else { return "couldn't press \(key)" }
            e.flags = flags
            e.post(tap: .cghidEventTap)
        }
        return nil
    }
}
