import AppKit
import CoreGraphics
import SwiftUI
import WebKit

/// Development aid: with CLAUDE_NOTCH_SNAPSHOT=<dir> in the environment, the app writes PNGs of its views
/// (open panel, every Settings section) and quits. Everything renders OFF-SCREEN ("never disturb the user"):
/// no notch, no menu bar item, no window they can see, never activated, no sound. The app runs in this
/// mode with activation policy .prohibited (ClaudeNotchApp skips all its normal UI).
@MainActor
enum DebugSnapshot {
    static var dir: URL? {
        ProcessInfo.processInfo.environment["CLAUDE_NOTCH_SNAPSHOT"].map { URL(fileURLWithPath: $0, isDirectory: true) }
    }

    static func run(notch: NotchController, hub: HubClient) {
        guard let dir else { return }
        NSApp.setActivationPolicy(.prohibited)  // can never come to the front or take focus
        NSApp.appearance = NSAppearance(named: ProcessInfo.processInfo.environment["CLAUDE_NOTCH_LIGHT"] != nil ? .aqua : .darkAqua)
        Task {
            try? await Task.sleep(for: .seconds(2))
            if ProcessInfo.processInfo.environment["CLAUDE_NOTCH_PANEL_TEST"] != nil || !BackdropShots.backdrops.isEmpty {
                // These drive the real notch on the real screen: only when the user is away (ScreenGate), else they wait.
                guard ScreenGate.realScreenOK() else {
                    Log.d("real-screen check skipped: \(ScreenGate.why())")
                    exit(3)
                }
                if ProcessInfo.processInfo.environment["CLAUDE_NOTCH_PANEL_TEST"] != nil {
                    exit(await PanelSelfTest.run(notch: notch, dir: dir) ? 0 : 1)
                }
                await BackdropShots.run(notch: notch, hub: hub, dir: dir)
                NSApp.terminate(nil)
                return
            }
            await hub.refreshPanel()
            if ProcessInfo.processInfo.environment["CLAUDE_NOTCH_DEMO"] != nil { hub.showDemoTranscript() }
            let panel = PanelView(hub: hub, onClose: {}, onSettings: { _ in }, onPin: { _ in }, onQuit: {},
                                  onResize: { _ in }, onResizeEnd: {}, source: .top)
                .frame(width: PanelLayout.width, height: PanelLayout.defaultSize.height)
            let tab = ProcessInfo.processInfo.environment["CLAUDE_NOTCH_TAB"]
            await Offscreen.render(panel, size: PanelLayout.defaultSize, to: dir.appending(path: tab.map { "panel-\($0).png" } ?? "panel.png"),
                                   settle: .seconds(5))  // the first web view starts WebKit's processes
            if tab == nil {
                for section in SettingsSection.allCases {
                    let name = section.rawValue.lowercased().replacingOccurrences(of: " ", with: "-")
                    await Offscreen.render(SettingsView(hub: hub, notch: notch, initial: section),
                                           size: CGSize(width: 720, height: SettingsView.height),
                                           to: dir.appending(path: "settings-\(name).png"))
                }
                // Speed with synthetic turns (the real one would show the user's commands)
                await Offscreen.render(ScrollView { SpeedSection(hub: hub, sample: .sample).padding(28) }
                                           .frame(width: 720, height: 1240).background(Color(nsColor: .windowBackgroundColor)),
                                       size: CGSize(width: 720, height: 1240), to: dir.appending(path: "speed-sample.png"))
                // Activity's background queue and the Scheduled list unfolded
                await Offscreen.render(ScrollView { VStack(alignment: .leading, spacing: 14) {
                                           BackgroundList(hub: hub); ScheduledList(hub: hub, open: true) }.padding(20) }
                                           .frame(width: PanelLayout.width, height: 760).background(Color(nsColor: .windowBackgroundColor)),
                                       size: CGSize(width: PanelLayout.width, height: 760), to: dir.appending(path: "background.png"))
            }
            await writeSettingsWindow(hub: hub, notch: notch, to: dir.appending(path: "settings-window.png"))
            // Scrolled into the options, Maxie stays pinned at the top, compact
            await writeSettingsWindow(hub: hub, notch: notch, section: .maxie, scroll: 520,
                                      to: dir.appending(path: "settings-maxie-scrolled.png"))
            // the Pause shortcut and the Pause / Quit buttons, at the bottom of General
            await writeSettingsWindow(hub: hub, notch: notch, section: .general, scroll: 420,
                                      to: dir.appending(path: "settings-general-scrolled.png"))
            // Automation with synthetic answers: Music quit but allowed earlier
            await Offscreen.render(AutomationSample().padding(28).frame(width: 720, height: 420)
                                       .background(Color(nsColor: .windowBackgroundColor)),
                                   size: CGSize(width: 720, height: 420), to: dir.appending(path: "automation-sample.png"))
            await Offscreen.render(ScreenSample().padding(28).frame(width: 720, height: 520)
                                       .background(Color(nsColor: .windowBackgroundColor)),
                                   size: CGSize(width: 720, height: 520), to: dir.appending(path: "screen-sample.png"))
            writeGlyphs(to: dir.appending(path: "menubar-glyphs.png"))
            await Offscreen.render(NotchStatesSheet(), size: NotchStatesSheet.size, to: dir.appending(path: "notch-states.png"))
            // what the notch says instead of silence, and a finished "Test wake word"
            await Offscreen.render(VoiceNoticeSheet(), size: VoiceNoticeSheet.size, to: dir.appending(path: "voice-notices.png"))
            await Offscreen.render(SunsetCard { WakeTestCard(hub: hub, sample: WakeTestCard.sampleMiss) }.padding(28)
                                       .frame(width: 720, height: 220).background(Color(nsColor: .windowBackgroundColor)),
                                   size: CGSize(width: 720, height: 220), to: dir.appending(path: "wake-test-sample.png"))
            writeNotchLayer(to: dir.appending(path: "notch-layer.png"))
            writeWatching(to: dir.appending(path: "notch-watching.png"))
            HoverTrace.write(to: dir.appending(path: "hover-trace.png"))
            NSApp.terminate(nil)
        }
    }

    /// The real notch renderer (Core Animation, MascotLayerView) for idle, listen and talk, at 4x: checks the live
    /// eyes line up with the frames the way the notch shows them.
    static func writeNotchLayer(to url: URL) {
        writeLayers([BotState.idle, .listen, .talk].map(notchView), to: url)
    }

    /// Maxie watching where the user works, as the notch draws it (4x). First as before (1 pt, upright),
    /// then the pointer to his left, below him, and to his right.
    static func writeWatching(to url: URL) {
        let before = notchView(.idle)
        before.pose(look: CGSize(width: 1, height: 0), lean: 0)
        let r = CursorLook.reach
        let now = [CGSize(width: -r, height: 0.5), CGSize(width: 0, height: r), CGSize(width: r, height: 0.5)].map { o in
            let v = notchView(.idle)
            v.follow(o)
            return v
        }
        writeLayers([before] + now, to: url)
    }

    private static let shotScale: CGFloat = 4

    private static func notchView(_ state: BotState) -> MascotLayerView {
        let slot = MascotLibrary.shared.slotSize, k = shotScale
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: slot.width * k, height: slot.height * k))
        v.scale = 1.5  // the 3x frames
        v.state = state
        v.scale = k
        v.layout()
        return v
    }

    private static func writeLayers(_ views: [MascotLayerView], to url: URL) {
        let slot = MascotLibrary.shared.slotSize, k = shotScale
        let size = CGSize(width: slot.width * k * CGFloat(views.count), height: slot.height * k)
        guard let ctx = CGContext(data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
        ctx.setFillColor(NSColor.black.cgColor)
        ctx.fill(CGRect(origin: .zero, size: size))
        for (n, v) in views.enumerated() {
            ctx.saveGState()
            ctx.translateBy(x: CGFloat(n) * slot.width * k, y: size.height)
            ctx.scaleBy(x: 1, y: -1)  // the view is flipped (top-left origin)
            v.layer?.render(in: ctx)
            ctx.restoreGState()
        }
        guard let image = ctx.makeImage() else { return }
        try? NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: url)
    }

    /// The menu-bar glyph in every look (Maxie, Classic; awake, asleep, paused) at 4x on a light and a dark bar.
    static func writeGlyphs(to url: URL) {
        let looks = [(false, false), (true, false), (false, true)]
        let cell = NSSize(width: 18 * 4 + 24, height: 16 * 4 + 16)
        let size = NSSize(width: cell.width * 6, height: cell.height * 2)
        let img = NSImage(size: size, flipped: false) { _ in
            for (row, dark) in [false, true].enumerated() {
                (dark ? NSColor(white: 0.12, alpha: 1) : NSColor(white: 0.93, alpha: 1)).setFill()
                NSRect(x: 0, y: CGFloat(row) * cell.height, width: size.width, height: cell.height).fill()
                for (col, (classic, look)) in [false, true].flatMap({ c in looks.map { (c, $0) } }).enumerated() {
                    let glyph = BotGlyph.image(asleep: look.0, muted: look.1, classic: classic)
                    let tinted = NSImage(size: NSSize(width: 72, height: 64), flipped: false) { r in
                        glyph.draw(in: r)
                        (dark ? NSColor.white : NSColor.black).set()
                        r.fill(using: .sourceAtop)
                        return true
                    }
                    tinted.draw(in: NSRect(x: CGFloat(col) * cell.width + 12, y: CGFloat(row) * cell.height + 8, width: 72, height: 64))
                }
            }
            return true
        }
        guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return }
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }

    /// The real Settings window with its title bar, the traffic lights in as if the pointer were near the top.
    /// Drawn from the window's frame view, off every screen and fully transparent like the rest.
    static func writeSettingsWindow(hub: HubClient, notch: NotchController, section: SettingsSection = .general,
                                    scroll: CGFloat = 0, to url: URL) async {
        let host = FirstClickHostingView(rootView: SettingsView(hub: hub, notch: notch, initial: section))
        host.sizingOptions = []
        let w = SettingsWindow.makeWindow(content: host)
        w.alphaValue = 0
        w.ignoresMouseEvents = true
        w.setFrameOrigin(CGPoint(x: -40_000, y: -40_000))
        w.orderBack(nil)
        [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton].forEach { w.standardWindowButton($0)?.alphaValue = 1 }
        try? await Task.sleep(for: .seconds(scroll > 0 ? 5 : 2))  // the Maxie page's web view starts WebKit
        if scroll > 0, let sv = firstScrollView(in: host) {
            sv.contentView.scroll(to: NSPoint(x: 0, y: scroll))
            sv.reflectScrolledClipView(sv.contentView)
            try? await Task.sleep(for: .seconds(1))
        }
        let stills = await Offscreen.webStills(in: host)
        if let frame = w.contentView?.superview { frame.layoutSubtreeIfNeeded(); write(frame, to: url) }
        stills.forEach { $0.removeFromSuperview() }
        w.orderOut(nil)
        w.close()
    }

    private static func firstScrollView(in view: NSView) -> NSScrollView? {
        (view as? NSScrollView) ?? view.subviews.lazy.compactMap(firstScrollView(in:)).first
    }

    static func write(_ v: NSView, to url: URL) {
        guard let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return }
        v.cacheDisplay(in: v.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}

/// Draws a view to a PNG without showing it: a borderless, fully transparent, click-through window parked
/// far outside every screen, never key, never in front of the user's work. AppKit controls (switches, pickers,
/// text fields) need a window to lay out, which plain ImageRenderer can't give them.
@MainActor
enum Offscreen {
    static func render(_ view: some View, size: CGSize, to url: URL, settle: Duration = .seconds(2)) async {
        let w = NSWindow(contentRect: NSRect(origin: CGPoint(x: -40_000, y: -40_000), size: size), styleMask: [.borderless],
                         backing: .buffered, defer: false)
        w.isOpaque = false
        w.backgroundColor = .clear
        w.alphaValue = 0           // even the window server shows nothing
        w.ignoresMouseEvents = true
        w.isReleasedWhenClosed = false
        w.collectionBehavior = [.transient, .ignoresCycle, .canJoinAllSpaces]
        let host = NSHostingView(rootView: view.environment(\.colorScheme, NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .aqua ? .light : .dark))
        host.frame = NSRect(origin: .zero, size: size)
        w.contentView = host
        w.setFrameOrigin(CGPoint(x: -40_000, y: -40_000))
        w.orderBack(nil)           // in the window list (so SwiftUI lays out), behind everything, off every screen
        try? await Task.sleep(for: settle)
        host.layoutSubtreeIfNeeded()
        let stills = await webStills(in: host)  // web views draw out of process; cacheDisplay can't see them
        DebugSnapshot.write(host, to: url)
        stills.forEach { $0.removeFromSuperview() }
        w.orderOut(nil)
        w.close()
    }
}

/// Every bot state as the notch draws it (the pre-rendered loops plus live eyes), at 3x, on the menu bar's black.
struct NotchStatesSheet: View {
    static let zoom: CGFloat = 3
    static var cell: CGSize { let s = MascotLibrary.shared.slotSize; return CGSize(width: s.width * zoom + 24, height: s.height * zoom + 40) }
    static var size: CGSize { CGSize(width: cell.width * 6, height: cell.height * 2) }

    var body: some View {
        let slot = MascotLibrary.shared.slotSize
        LazyVGrid(columns: Array(repeating: GridItem(.fixed(Self.cell.width), spacing: 0), count: 6), spacing: 0) {
            ForEach(BotState.allCases, id: \.self) { state in
                VStack(spacing: 4) {
                    MascotView(state: state).frame(width: slot.width, height: slot.height).scaleEffect(Self.zoom)
                        .frame(width: slot.width * Self.zoom, height: slot.height * Self.zoom)
                    Text(state.label).font(.system(size: 11)).foregroundStyle(.white.opacity(0.7))
                }
                .frame(width: Self.cell.width, height: Self.cell.height)
            }
        }
        .frame(width: Self.size.width, height: Self.size.height, alignment: .topLeading)
        .background(Color.black)
    }
}

extension Offscreen {
    /// Lays a still of every web view (the live bot) over it, so the PNG shows what the page drew.
    static func webStills(in root: NSView) async -> [NSView] {
        var stills: [NSView] = []
        for web in webViews(in: root) {
            guard let image = try? await web.takeSnapshot(configuration: nil) else { continue }
            let still = NSImageView(frame: web.bounds)
            still.image = image
            still.imageScaling = .scaleProportionallyUpOrDown
            web.addSubview(still)
            stills.append(still)
        }
        return stills
    }

    private static func webViews(in view: NSView) -> [WKWebView] {
        (view as? WKWebView).map { [$0] } ?? view.subviews.flatMap(webViews(in:))
    }
}

/// When may a build check use the REAL screen (permission prompts, notch placement, drag)? Only when the user has
/// been away ≥ 10 min and nothing is full-screen (a movie, a call, a game). Otherwise the check waits
/// ("never disturb the user").
enum ScreenGate {
    static let idleNeeded: TimeInterval = 600

    static var idleSeconds: TimeInterval {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: CGEventType(rawValue: ~0)!)
    }

    @MainActor static var somethingFullScreen: Bool {
        let screens = NSScreen.screens.map { $0.frame.size }
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        return windows.contains { w in
            guard (w[kCGWindowLayer as String] as? Int) == 0,
                  let b = w[kCGWindowBounds as String] as? [String: CGFloat] else { return false }
            return screens.contains { $0.width == b["Width"] && $0.height == b["Height"] }
        }
    }

    @MainActor static func realScreenOK() -> Bool { idleSeconds >= idleNeeded && !somethingFullScreen }

    @MainActor static func why() -> String {
        somethingFullScreen ? "something is full-screen" : "the user was active \(Int(idleSeconds)) s ago (needs \(Int(idleNeeded)) s)"
    }
}

/// Settings › Permissions › Automation with fixed answers, for the snapshot: running and allowed, quit and
/// remembered, quit and never asked, turned off.
private struct AutomationSample: View {
    var body: some View {
        let now = Date()
        let rows: [(String, AutomationStatus)] = [
            ("Notes", AutomationStatus(grant: .allowed, checked: nil)),
            ("Music", AutomationStatus(grant: .allowed, checked: now.addingTimeInterval(-2 * 3600))),
            ("Mail", AutomationStatus(grant: .allowed, checked: now.addingTimeInterval(-26 * 3600))),
            ("Messages", AutomationStatus(grant: .notAsked, checked: nil)),
            ("Reminders", AutomationStatus(grant: .denied, checked: nil)),
        ]
        SunsetCard {
            Label("Automation", systemImage: "applescript").font(.headline)
            ForEach(rows, id: \.0) { name, status in
                AutomationRow(target: AutomationTarget.all.first { $0.name == name }!, status: status)
            }
        }
    }
}

/// Settings › Permissions › Screen Recording in each state macOS can leave it in after a new signature:
/// sent to System Settings, switched on but waiting for a reopen, an older build's switch.
private struct ScreenSample: View {
    var body: some View {
        VStack(spacing: 14) {
            ForEach([ScreenAccess.Advice.turnOnInSettings, .reopen, .removeOld], id: \.self) { advice in
                SunsetCard {
                    SettingRow(title: Permission.screen.title, subtitle: Permission.screen.why) {
                        PermissionAction(grant: .notAsked, ask: {}, pane: Permission.screen.pane)
                    }
                    ScreenHint(advice: advice)
                }
            }
        }
    }
}
