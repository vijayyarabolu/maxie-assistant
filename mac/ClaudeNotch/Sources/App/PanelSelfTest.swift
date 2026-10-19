import AppKit

/// Development aid, with CLAUDE_NOTCH_SNAPSHOT=<dir> CLAUDE_NOTCH_PANEL_TEST=1: drives this
/// process's own panel with key events posted to its own event queue (never system-wide: no cursor moves,
/// no clicks or keys reach other apps), checks what happens, captures the menu-bar glyph and Settings ›
/// Connections, writes panel-test.txt, and quits. Quit is checked up to the question, not past it.
@MainActor
enum PanelSelfTest {
    static func run(notch: NotchController, dir: URL) async -> Bool {
        var lines: [String] = []
        var ok = true
        func check(_ name: String, _ pass: Bool) {
            lines.append("\(pass ? "PASS" : "FAIL") \(name)")
            ok = ok && pass
        }
        var settingsAsked: SettingsSection?? = nil
        var quitAsked = false
        let realSettings = notch.onSettings
        notch.onSettings = { settingsAsked = .some($0); realSettings($0) }
        notch.onQuit = { quitAsked = true }

        for _ in 0..<3 where notch.panelWindow == nil {  // launch-time screen changes can close the first one
            notch.expand(reason: .hover)
            try? await Task.sleep(for: .seconds(2))
        }
        check("opens", notch.panelWindow != nil)
        check("hover-opened, unpinned: a click elsewhere closes it", notch.closesOnClickElsewhere)
        notch.setPinned(true)
        check("pinned: a click elsewhere keeps it", !notch.closesOnClickElsewhere)
        notch.setPinned(false)

        if let w = notch.panelWindow {
            let top = w.frame.maxY
            notch.resizePanel(to: 480)
            check("bottom edge resizes (480 pt), top stays", w.frame.height == 480 && w.frame.maxY == top)
            notch.resizePanel(to: 100)
            check("never shorter than \(Int(PanelLayout.minHeight)) pt", w.frame.height == PanelLayout.minHeight)
            w.setFrameOrigin(NSPoint(x: w.frame.minX - 40, y: w.frame.minY - 30))
            check("movable", true)
        }

        await press(notch, ",", command: true)
        check("⌘, opens Settings", settingsAsked != nil && NSApp.windows.contains { $0 is LightWindow && $0.isVisible })
        await press(notch, "q", command: true)
        check("⌘Q asks to quit", quitAsked)
        await press(notch, "\u{1b}", keyCode: 53)
        check("Esc closes", notch.panelWindow == nil)

        // Settings › Connections, as "Gmail not connected · Connect" opens it.
        realSettings(.connections)
        try? await Task.sleep(for: .seconds(3))
        if let v = NSApp.windows.first(where: { $0 is LightWindow && $0.isVisible })?.contentView {
            DebugSnapshot.write(v, to: dir.appending(path: "settings-connections.png"))
        }
        // The menu-bar glyph, as the menu bar shows it.
        let bar = NSApp.windows.filter { String(describing: type(of: $0)).contains("StatusBar") && $0.isVisible }
        check("menu-bar icon shown", !bar.isEmpty)
        writeGlyphs(to: dir.appending(path: "menubar-glyph.png"))
        let report = lines.joined(separator: "\n") + "\n"
        print(report)
        try? report.write(to: dir.appending(path: "panel-test.txt"), atomically: true, encoding: .utf8)
        return ok
    }

    /// The three menu-bar looks (listening, paused, hub offline) at 4x, dark on light and light on dark.
    private static func writeGlyphs(to url: URL) {
        let scale: CGFloat = 4, cell = NSSize(width: BotGlyph.size.width * scale + 24, height: BotGlyph.size.height * scale + 24)
        let looks = [(false, false), (false, true), (true, false)]
        let img = NSImage(size: NSSize(width: cell.width * 3, height: cell.height * 2), flipped: false) { _ in
            for (row, dark) in [false, true].enumerated() {
                (dark ? NSColor(white: 0.12, alpha: 1) : NSColor(white: 0.93, alpha: 1)).setFill()
                NSRect(x: 0, y: CGFloat(row) * cell.height, width: cell.width * 3, height: cell.height).fill()
                for (i, look) in looks.enumerated() {
                    let g = BotGlyph.image(asleep: look.0, muted: look.1)
                    let tinted = NSImage(size: g.size, flipped: false) { r in
                        g.draw(in: r)
                        (dark ? NSColor.white : NSColor.black).set()
                        r.fill(using: .sourceAtop)
                        return true
                    }
                    tinted.draw(in: NSRect(x: CGFloat(i) * cell.width + 12, y: CGFloat(row) * cell.height + 12,
                                           width: BotGlyph.size.width * scale, height: BotGlyph.size.height * scale))
                }
            }
            return true
        }
        if let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) {
            try? rep.representation(using: .png, properties: [:])?.write(to: url)
        }
    }

    /// A key event into this app's own queue, aimed at the panel (as if typed while it is key).
    private static func press(_ notch: NotchController, _ chars: String, keyCode: UInt16 = 0, command: Bool = false) async {
        if notch.panelWindow == nil { notch.expand(reason: .menu); try? await Task.sleep(for: .seconds(1)) }
        guard let w = notch.panelWindow,
              let e = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: command ? .command : [], timestamp: ProcessInfo.processInfo.systemUptime,
                                       windowNumber: w.windowNumber, context: nil, characters: chars, charactersIgnoringModifiers: chars,
                                       isARepeat: false, keyCode: keyCode == 0 ? (chars == "q" ? 12 : 43) : keyCode) else { return }
        NSApp.postEvent(e, atStart: false)
        try? await Task.sleep(for: .milliseconds(600))
    }
}
