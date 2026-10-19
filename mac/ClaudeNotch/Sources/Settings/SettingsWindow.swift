import AppKit
import SwiftUI

/// Which Settings section is showing. One SettingsView lives for the window's lifetime and follows this model,
/// so opening Settings on a section never rebuilds the views or the live preview. Remembers the
/// last section the user used; a nil `defaults` (snapshots, tests) remembers nothing.
@MainActor
@Observable
final class SettingsModel {
    static let lastKey = "settings.lastSection"
    @ObservationIgnored private let defaults: UserDefaults?

    var section: SettingsSection {
        didSet { defaults?.set(section.rawValue, forKey: Self.lastKey) }
    }

    init(section: SettingsSection? = nil, defaults: UserDefaults? = .standard) {
        self.defaults = defaults
        self.section = section ?? defaults?.string(forKey: Self.lastKey).flatMap(SettingsSection.init(rawValue:)) ?? .maxie
    }
}

/// Where Settings opens: never on top of the notch panel. Beside it (left, then right) or below it, top
/// edges lined up; centred when there is no panel. nil: it can't avoid the panel on this screen.
enum SettingsPlacement {
    static let gap: CGFloat = 12

    static func frame(size: CGSize, avoiding panel: CGRect?, in screen: CGRect) -> CGRect? {
        guard let panel, panel.intersects(screen) else {
            return CGRect(x: screen.midX - size.width / 2, y: screen.midY - size.height / 2, width: size.width, height: size.height)
        }
        let top = min(panel.maxY, screen.maxY) - size.height
        let candidates = [
            CGPoint(x: panel.minX - gap - size.width, y: top),   // left of the panel
            CGPoint(x: panel.maxX + gap, y: top),                // right of it
            CGPoint(x: panel.midX - size.width / 2, y: panel.minY - gap - size.height),  // below it
        ]
        for origin in candidates {
            let y = max(screen.minY, min(origin.y, screen.maxY - size.height))
            let f = CGRect(origin: CGPoint(x: origin.x, y: y), size: size)
            if screen.contains(f) && !f.intersects(panel) { return f }
        }
        return nil
    }
}

/// The Settings window (gear in the panel's More menu, "Settings…" in the menu bar, ⌘,): the same field of
/// light as the panel, and a normal Mac window: drag its top to move it, resize it from any edge,
/// minimize or close it with the traffic lights, which fade in only while the pointer is near the top. Esc, ⌘W
/// or "Done" close it too.
@MainActor
final class SettingsWindow {
    static let size = CGSize(width: 720, height: 560)
    /// The list and the 500-pt column side by side, and enough height for a section's title and first card.
    static let minSize = CGSize(width: 720, height: 440)
    private let hub: HubClient
    private let notch: NotchController
    private let model = SettingsModel()
    private var window: NSWindow?

    init(hub: HubClient, notch: NotchController) {
        self.hub = hub
        self.notch = notch
        // After a permission prompt or a System Settings trip, back to the front on the same page.
        backObserver = NotificationCenter.default.addObserver(forName: .maxieComesBack, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.comeBack() }
        }
    }

    private var backObserver: NSObjectProtocol?

    private func comeBack() {
        guard window?.isVisible == true else { return }  // Settings was closed meanwhile: nothing to bring back
        show()
    }

    /// A titled window with its title bar made invisible, so the light fills it all. It moves from the title bar
    /// only: never "movable by background", which turned every plain button into a drag handle.
    static func makeWindow(content: NSView) -> LightWindow {
        let w = LightWindow(contentRect: NSRect(origin: .zero, size: size),
                            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                            backing: .buffered, defer: false)
        w.title = "Maxie Settings"
        w.titleVisibility = .hidden
        w.titlebarAppearsTransparent = true
        w.contentMinSize = minSize
        w.isOpaque = false
        w.backgroundColor = .clear
        w.hasShadow = false
        w.isMovableByWindowBackground = false
        w.isReleasedWhenClosed = false
        content.frame = NSRect(origin: .zero, size: size)
        w.contentView = content
        w.trafficLights = TrafficLights(window: w)
        return w
    }

    /// Open Settings; with a section ("Gmail not connected · Connect" → Connections), on that section.
    func show(section: SettingsSection? = nil) {
        if let section { model.section = section }
        let w = window ?? {
            let host = FirstClickHostingView(rootView: SettingsView(hub: hub, notch: notch, model: model,
                                                                    onDone: { [weak self] in self?.window?.orderOut(nil) }))
            host.sizingOptions = []
            let w = Self.makeWindow(content: host)
            window = w
            return w
        }()
        if !w.isVisible { place(w) }
        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }

    func close() { window?.orderOut(nil) }

    /// Beside or below the panel, never under it; if that can't fit, an unpinned panel closes and Settings centres.
    private func place(_ w: NSWindow) {
        let screen = (notch.panelFrame.flatMap { f in NSScreen.screens.first { $0.frame.intersects(f) } } ?? NSScreen.main)?.visibleFrame
            ?? CGRect(origin: .zero, size: Self.size)
        let size = w.frame.size  // the size the user left it at
        if let f = SettingsPlacement.frame(size: size, avoiding: notch.panelFrame, in: screen) {
            w.setFrame(f, display: false)
            return
        }
        if !notch.panelPinned { notch.collapse() }
        w.setFrame(SettingsPlacement.frame(size: size, avoiding: nil, in: screen)!, display: false)
    }
}

/// A window that takes keys (typing in fields, Esc, ⌘W).
final class LightWindow: NSWindow {
    /// ⌘Q here asks first, like the panel (AppDelegate.quitAsking).
    static var onQuit: () -> Void = {}
    var trafficLights: TrafficLights?

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    override func cancelOperation(_ sender: Any?) { orderOut(nil) }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers == "w" {
            orderOut(nil)
            return true
        }
        if PanelKey(event) == .quit {
            Self.onQuit()
            return true
        }
        return super.performKeyEquivalent(with: event)
    }
}

/// His design: the close, minimize and zoom buttons stay invisible and fade in only while the
/// pointer is near the top of the window, then fade out again.
@MainActor
final class TrafficLights: NSResponder {
    /// How far down from the top edge the pointer brings them in.
    nonisolated static let band: CGFloat = 44
    private static let fade = 0.18
    private weak var window: NSWindow?
    private var shown = true

    init(window: NSWindow) {
        self.window = window
        super.init()
        set(false, animated: false)
        // activeAlways: Maxie is often not the active app; inVisibleRect follows the user's resizes
        window.contentView?.addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited,
                                                                                   .activeAlways, .inVisibleRect],
                                                           owner: self))
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    nonisolated static func visible(mouse: CGPoint, in frame: CGRect) -> Bool {
        frame.contains(mouse) && mouse.y >= frame.maxY - band
    }

    override func mouseMoved(with event: NSEvent) { follow() }
    override func mouseEntered(with event: NSEvent) { follow() }
    override func mouseExited(with event: NSEvent) { set(false) }

    private func follow() {
        guard let window else { return }
        set(Self.visible(mouse: NSEvent.mouseLocation, in: window.frame))
    }

    private func set(_ on: Bool, animated: Bool = true) {
        guard on != shown else { return }
        shown = on
        let buttons = [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton].compactMap { window?.standardWindowButton($0) }
        guard animated, !Motion.reduce else { return buttons.forEach { $0.alphaValue = on ? 1 : 0 } }
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = Self.fade
            buttons.forEach { $0.animator().alphaValue = on ? 1 : 0 }
        }
    }
}
