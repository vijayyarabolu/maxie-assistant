import AppKit
import SwiftUI

/// Two borderless panels: Maxie (at the notch, tucked into an edge, or anywhere you drop it) and the
/// bigger panel that opens next to it. Opens on hover, on "Hey Max" (state listen), from the menu or
/// ⌥Space; closes on Esc, the × button, or (unpinned) a click elsewhere or a switch to another app.
/// The panel springs out of Maxie and tucks back into him; Maxie steps aside from the window
/// the user is working in, goes up and out for a full-screen app, and comes back after (StepAside, Motion).
/// Drag its top to move it, its bottom edge to resize it; both are remembered. ⌘, Settings, ⌘Q quits.
@MainActor
final class NotchController {
    enum Reason { case hover, wake, menu, hotkey }

    static var notchSize: CGSize { MascotLibrary.shared.slotSize }  // Maxie 36x42 pt, Classic 30x50
    static var freeScale: CGFloat { MaxieLook.freeScale() }  // outside the notch (Settings › Maxie › Size), from the 3x frames
    static let panelSize = PanelLayout.defaultSize

    var onSettings: (SettingsSection?) -> Void = { _ in }
    var onSettingsClose: () -> Void = {}
    var onQuit: () -> Void = {}

    private let hub: HubClient
    private var mini: NSPanel?
    private var drag: DragView?
    private var big: NSPanel?
    private var openedBy: Reason?
    private var clickMonitor: Any?
    private var localClickMonitor: Any?
    private var keyMonitor: Any?
    private var moveObserver: NSObjectProtocol?
    private var observers: [NSObjectProtocol] = []
    private var remoteObserver: NSObjectProtocol?
    private var dragOffset = CGPoint.zero
    private var peeking = false
    private var stepped: StepAside.Move = .stay  // where Maxie is because of what covers his home
    private var acceptedCover: CGRect?  // the window the user dropped Maxie onto: it never moves him
    private var acceptPending = false   // a drop happened: the next cover check sets acceptedCover
    private var placedPanel: CGRect?  // where Maxie put the chat himself; only other spots are the user's
    private var coverMonitor: Any?
    private var coverTimer: Timer?
    private var coverCheck: Task<Void, Never>?
    static let freeCheckEvery: TimeInterval = 4  // free Maxie: also look now and then (a window moved by keys)

    private let store = PlacementStore()
    private let noticeLine = NoticeLine()
    private var layout = ScreenLayout(NSScreen.screens.map { (NotchController.id(of: $0), $0.frame) })
    private var screensTask: Task<Void, Never>?
    private(set) var placement = BotPlacement.notch(on: 0)
    private let defaults = UserDefaults.standard

    /// Settings › General. "notch": Maxie stays home; "free": drag it anywhere.
    var mode: String {
        get { defaults.string(forKey: "botMode") ?? "notch" }
        set { defaults.set(newValue, forKey: "botMode"); applyMode() }
    }
    var hideWhenFullscreen: Bool {
        get { defaults.object(forKey: "hideWhenFullscreen") as? Bool ?? true }
        set { defaults.set(newValue, forKey: "hideWhenFullscreen"); checkCover() }
    }

    /// Other local processes (the voice helper, a Shortcut, a test) can drive the panel:
    /// `DistributedNotificationCenter` name "dev.maxie.notch.panel", object "open" | "close" | "toggle" | "screen" | "send:<text>".
    static let remoteName = Notification.Name("dev.maxie.notch.panel")

    init(hub: HubClient) {
        self.hub = hub
        remoteObserver = DistributedNotificationCenter.default().addObserver(
            forName: Self.remoteName, object: nil, queue: .main) { [weak self] n in
            let what = n.object as? String
            Log.d("remote: \(what ?? "nil")")
            MainActor.assumeIsolated {
                guard let self else { return }
                switch what {
                case "open": self.expand(reason: .menu)
                case "close": self.collapse()
                case "home": self.goHome()
                case "pause": self.hub.setPaused(true)   // tests and Shortcuts
                case "resume": self.hub.setPaused(false)
                case "screen":
                    self.expand(reason: .menu)
                    self.hub.lookAtScreen(question: "What's on my screen?")
                case let s? where s.hasPrefix("send:"):  // a Shortcut or a test types for you
                    self.expand(reason: .menu)
                    self.hub.send(text: String(s.dropFirst(5)))
                case let s? where s.hasPrefix("settings:"):  // "settings:Maxie" (perf runs, Shortcuts): open Settings there
                    self.onSettings(SettingsSection(rawValue: String(s.dropFirst(9))))
                case "settings-close": self.onSettingsClose()
                case let s? where s.hasPrefix("moveto:"):  // "moveto:x,y" (tests): drop Maxie there
                    let parts = s.dropFirst(7).split(separator: ",").compactMap { Double($0) }
                    if parts.count == 2 { self.drop(at: CGPoint(x: parts[0], y: parts[1])) }
                default: self.big == nil ? self.expand(reason: .menu) : self.collapse()
                }
            }
        }
    }

    /// For the self-test: a click elsewhere would close the panel.
    var closesOnClickElsewhere: Bool { clickMonitor != nil }
    /// Where the panel is (Settings opens beside it, never under it); nil when it's closed.
    var panelFrame: CGRect? { big?.frame }
    var panelPinned: Bool { defaults.bool(forKey: PanelView.pinnedKey) }
    private var following = false  // the panel rides along while the user drags Maxie
    private let stroll = Stroll()  // while Maxie works
    private var hoverGate = HoverGate()  // a dead zone and a short settle
    private var hoverTimer: Timer?
    static let hoverEvery: TimeInterval = 0.05

    /// For DebugSnapshot only.
    var miniView: NSView? { mini?.contentView }
    var panelView: NSView? { big?.contentView }
    var panelWindow: NSWindow? { big }

    // MARK: - Screens

    /// The screen with a notch (the built-in display), else the main one.
    private var notchScreen: NSScreen {
        NSScreen.screens.first { $0.safeAreaInsets.top > 0 } ?? NSScreen.main ?? NSScreen.screens[0]
    }

    static func id(of screen: NSScreen) -> UInt32 {
        (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
    }

    private var screen: NSScreen {
        NSScreen.screens.first { Self.id(of: $0) == placement.displayID } ?? notchScreen
    }

    private var botSize: CGSize {
        placement.isAtNotch ? Self.notchSize : CGSize(width: Self.notchSize.width * Self.freeScale, height: Self.notchSize.height * Self.freeScale)
    }

    /// Home: just right of the notch, top-aligned with the screen edge.
    private func home(on s: NSScreen) -> CGRect {
        let size = Self.notchSize
        let notchRight = s.auxiliaryTopRightArea?.minX ?? (s.frame.midX + 110)
        return CGRect(x: notchRight + 6, y: s.frame.maxY - size.height - 1, width: size.width, height: size.height)
    }

    private var botFrame: CGRect {
        BotGeometry.frame(for: placement, size: botSize, in: screen.visibleFrame.union(screen.frame), home: home(on: screen), peeking: peeking)
    }

    // MARK: - Showing

    func show() {
        stepped = .stay
        let ids = NSScreen.screens.map(Self.id(of:))
        placement = mode == "free" ? store.restore(connected: ids, main: Self.id(of: notchScreen)) : .notch(on: Self.id(of: notchScreen))
        if !ids.contains(placement.displayID) { placement = .notch(on: Self.id(of: notchScreen)) }
        Log.d("show: \(placement) on \(screen.frame)")
        let frame = botFrame
        let panel = Self.makeBotWindow(frame)
        let dragView = DragView(frame: NSRect(origin: .zero, size: frame.size))
        dragView.onHover = { [weak self] _ in self?.watchHover() }
        dragView.onDragStart = { [weak self] in self?.dragStart() }
        dragView.onDrag = { [weak self] p in self?.dragTo(p) }
        dragView.onDrop = { [weak self] in self?.dropAtMouse() }
        dragView.onClick = { [weak self] in
            if self?.hub.paused == true { self?.hub.setPaused(false) }  // "click me to wake me"
            self?.expand(reason: .menu)
        }
        dragView.onDoubleClick = { [weak self] in self?.goHome() }
        dragView.canDrag = mode == "free"
        let host = NSHostingView(rootView: MiniBot(hub: hub, scale: placement.isAtNotch ? 1 : Self.freeScale,
                                                   onListen: { [weak self] in self?.expand(reason: .wake) },
                                                   onWork: { [weak self] in self?.working($0) },
                                                   onNotice: { [weak self] in self?.showNotice($0) }))
        host.frame = dragView.bounds
        host.autoresizingMask = [.width, .height]
        dragView.addSubview(host)
        panel.contentView = dragView
        panel.orderFrontRegardless()
        mini = panel
        drag = dragView
        stopHover()
        hoverGate = HoverGate()
        updateCursorLook()
        if observers.isEmpty {  // once: show() runs again on every display change
            let nc = NotificationCenter.default, ws = NSWorkspace.shared.notificationCenter
            observers = [
                nc.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.screensChanged() }
                },
                ws.addObserver(forName: NSWorkspace.screensDidWakeNotification, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.reposition() }
                },
                ws.addObserver(forName: NSWorkspace.activeSpaceDidChangeNotification, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.checkCover() }
                },
                nc.addObserver(forName: .maxieStepsAside, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.big?.level = PanelRules.level(steppedAside: true) }
                },
                nc.addObserver(forName: .maxieComesBack, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.floatPanel() }
                },
                nc.addObserver(forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.floatPanel() }
                },
                ws.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] note in
                    let pid = (note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication)?.processIdentifier
                    MainActor.assumeIsolated { self?.appActivated(pid) }
                },
                nc.addObserver(forName: .mascotChanged, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.reposition() }  // Maxie and Classic have different slot sizes
                },
            ]
        }
        watchCover()
        checkCover()
    }

    /// a voice notice. Under Maxie for a few seconds; with the chat open, as Maxie's line in it instead.
    private func showNotice(_ notice: VoiceNotice) {
        if big != nil {
            hub.note(notice.text)
            return
        }
        noticeLine.show(notice, under: mini?.frame ?? botFrame, in: screen.visibleFrame.union(screen.frame))
    }

    /// Settings › General: hide or show Maxie.
    func setMiniVisible(_ visible: Bool) {
        if visible, mini == nil { show() } else if !visible { Self.discard(mini); mini = nil }
    }

    func resetPosition() {
        store.reset()
        goHome()
    }

    private func applyMode() {
        drag?.canDrag = mode == "free"
        if mode == "notch" { goHome() }
        watchCover()
    }

    private func updateCursorLook() {
        let f = botFrame
        CursorLook.origin = CGPoint(x: f.midX, y: f.minY + f.height * 0.62)
    }

    /// macOS sends screen-parameter changes in bursts and for much more than a display change (~100 an hour on his
    /// Mac: the Dock, the menu bar, Spaces). Rebuilding Maxie's window for each cost 0.5–1.7 s on the main thread
    ///. Wait for the burst to settle; rebuild only when a display came, went or changed size.
    private func screensChanged() {
        screensTask?.cancel()
        screensTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(ScreenLayout.settle))
            guard !Task.isCancelled, let self else { return }
            let now = ScreenLayout(NSScreen.screens.map { (Self.id(of: $0), $0.frame) })
            if now != self.layout {
                self.layout = now
                self.reposition()
            } else if let mini = self.mini, mini.frame != self.botFrame {
                mini.setFrame(self.botFrame, display: true)
                self.attachPanel()
            }
        }
    }

    /// Screens changed, or Home / Size changed in Settings: rebuild Maxie; an open panel stays and re-attaches.
    private func reposition() {
        Log.d("reposition: screens \(NSScreen.screens.map(\.frame))")
        stroll.halt()
        Self.discard(mini)
        mini = nil
        show()
        attachPanel()
    }

    /// Put an open panel back next to Maxie, fully on screen (after a drag, a Home or Size change).
    private func attachPanel() {
        guard let big else { return }
        let bot = mini?.frame ?? botFrame
        let layout = PanelLayout.frame(saved: .init(height: big.frame.height), bot: bot, screen: screen.visibleFrame,
                                       screens: NSScreen.screens.map(\.visibleFrame), atNotch: placement.isAtNotch)
        placedPanel = layout.frame
        big.setFrame(layout.frame, display: true, animate: false)
    }

    // MARK: - Dragging, snapping, tucking

    private func dragStart() {
        stroll.halt()
        if let big, let mini, PanelRules.follows(openedByHover: openedBy == .hover, pinned: panelPinned) {
            mini.addChildWindow(big, ordered: .above)  // moves with him, no close and reopen
            following = true
        } else {
            collapse()
        }
        dragOffset = CGPoint(x: NSEvent.mouseLocation.x - (mini?.frame.minX ?? 0), y: NSEvent.mouseLocation.y - (mini?.frame.minY ?? 0))
        peeking = false
        drag?.wiggle(true)
    }

    private func dragTo(_ mouse: CGPoint) {
        guard let mini else { return }
        let s = NSScreen.screens.first { $0.frame.contains(mouse) } ?? screen
        let origin = BotGeometry.clamp(CGPoint(x: mouse.x - dragOffset.x, y: mouse.y - dragOffset.y), size: mini.frame.size, in: s.frame)
        mini.setFrameOrigin(origin)
    }

    private func dropAtMouse() {
        drag?.wiggle(false)
        guard let mini else { return }
        drop(at: mini.frame.origin)
    }

    /// Snap a dropped origin (screen coordinates) to home, an edge, or a free spot; remember it.
    func drop(at origin: CGPoint) {
        let s = NSScreen.screens.first { $0.frame.contains(CGPoint(x: origin.x + 1, y: origin.y + 1)) } ?? screen
        let size = mini?.frame.size ?? botSize
        placement = BotGeometry.snap(drop: origin, size: size, in: s.frame, home: home(on: s), display: Self.id(of: s))
        store.save(placement)
        Log.d("drop -> \(placement)")
        settle(bounce: true)
        acceptPending = true  // the user put him there on purpose: the next cover check takes that window as accepted
        checkCover()
    }

    /// Double-click: everything goes home. Maxie to the notch, an open chat back beside him (it
    /// forgets where it was dragged, keeps its height). The rest of the time the chat stays wherever the user puts it.
    func goHome() {
        placement = .notch(on: Self.id(of: notchScreen))
        store.save(placement)
        acceptedCover = nil
        if DebugSnapshot.dir == nil { PanelLayout.forgetSpot(in: defaults) }
        following = big != nil  // an open chat stays open and goes back beside him
        settle(bounce: true)
    }

    /// Rebuild Maxie at its placement (size changes between the notch and the free bot).
    private func settle(bounce: Bool) {
        peeking = false
        stroll.halt()
        if following, let big { mini?.removeChildWindow(big) }
        Self.discard(mini)
        mini = nil
        if following {
            following = false
            show()
            attachPanel()  // flips beside or below him as needed, always fully on screen
        } else {
            collapse()
            show()
        }
        if bounce { drag?.bounce() }
    }

    /// An enter or exit only starts a short look at the pointer; HoverGate decides, so a pointer on
    /// his edge, or his own slide moving the edge under it, no longer makes him jump back and forth.
    private func watchHover() {
        guard hoverTimer == nil else { return }
        let t = Timer(timeInterval: Self.hoverEvery, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.hoverTick() }
        }
        RunLoop.main.add(t, forMode: .common)
        hoverTimer = t
        hoverTick()
    }

    private func hoverTick() {
        guard let mini else { return stopHover() }
        let p = NSEvent.mouseLocation
        if let inside = hoverGate.update(p, frame: mini.frame, now: ProcessInfo.processInfo.systemUptime) { hover(inside) }
        if !hoverGate.watching(p, frame: mini.frame) { stopHover() }
    }

    private func stopHover() {
        hoverTimer?.invalidate()
        hoverTimer = nil
    }

    private func hover(_ inside: Bool) {
        guard !(drag?.isDragging ?? false) else { return }
        if placement.isTucked {
            if inside != peeking {
                peeking = inside
                slide()
            }
            return
        }
        if inside { expand(reason: .hover) }
    }

    private func slide() {
        guard let mini else { return }
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.18
            mini.animator().setFrame(botFrame, display: true)
        }
        updateCursorLook()
    }

    // MARK: - Stepping aside

    /// The user switched apps: an unpinned panel tucks away, and Maxie looks at what is under him now.
    private func appActivated(_ pid: pid_t?) {
        if big != nil, PanelRules.tucks(activated: pid, own: getpid(), pinned: panelPinned) { collapse(animated: true) }
        checkCover()
    }

    /// What can move a window over Maxie: a mouse-up anywhere (a drag or a resize ends), and in free mode a slow
    /// look every few seconds. Events, not a fast poll, so an idle Maxie stays idle.
    private func watchCover() {
        guard DebugSnapshot.dir == nil else { return }
        if coverMonitor == nil {
            coverMonitor = NSEvent.addGlobalMonitorForEvents(matching: .leftMouseUp) { [weak self] _ in
                MainActor.assumeIsolated { self?.checkCoverSoon() }
            }
        }
        coverTimer?.invalidate()
        coverTimer = nil
        guard mode == "free" else { return }
        let t = Timer(timeInterval: Self.freeCheckEvery, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.checkCover() }
        }
        t.tolerance = 1
        RunLoop.main.add(t, forMode: .common)
        coverTimer = t
    }

    private func checkCoverSoon() {
        coverCheck?.cancel()
        coverCheck = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(150))  // the window lands first
            if !Task.isCancelled { self?.checkCover() }
        }
    }

    /// Nothing covers what the user is working on: a full-screen app ("Hide when an app is fullscreen") or a window over
    /// the notch sends Maxie up and out; a window under a free Maxie moves him beside it; then back home. The window
    /// list is read off the main thread (WindowList), so a busy WindowServer never stalls Maxie.
    private func checkCover() {
        guard DebugSnapshot.dir == nil else { return }
        Task { [weak self] in
            let list = await WindowList.fresh()
            self?.checkCover(list)
        }
    }

    private func checkCover(_ list: [WindowList.Window]) {
        if acceptPending {  // he just dropped Maxie: the window under him now is one he chose (see drop)
            acceptPending = false
            acceptedCover = StepAside.cover(botFrame, StepAside.frontWindows(list))
        }
        guard let mini, Self.mayStepAside(peeking: peeking, dragging: drag?.isDragging ?? false, following: following)
        else { return }
        let home = botFrame
        let move = StepAside.move(bot: home, windows: StepAside.frontWindows(list),
                                  fullscreen: hideWhenFullscreen && Self.fullscreenWindowPresent(list),
                                  atNotch: placement.isAtNotch, screen: screen.frame, accepted: acceptedCover)
        if stroll.isWalking {  // one owner of where he is: the stroll, until something covers him
            guard move != .stay else { return }
            stroll.halt()
        }
        guard move != stepped else { return }
        let was = stepped
        stepped = move
        Log.d("step aside: \(move)")
        let d = Motion.duration(Motion.step)
        switch move {
        case .hide:
            collapse(animated: true)
            let up = Motion.reduce ? home : home.offsetBy(dx: 0, dy: home.height * 0.5)
            Motion.animate(mini, to: up, alpha: 0, duration: d, curve: Motion.away) { [weak self] in
                if self?.stepped == .hide { mini.orderOut(nil) }
            }
        case .aside(let f):
            if was == .hide { mini.orderFrontRegardless() }
            Motion.animate(mini, to: f, alpha: 1, duration: d)
        case .stay:
            if was == .hide { mini.orderFrontRegardless() }
            Motion.animate(mini, to: home, alpha: 1, duration: d)
        }
    }

    /// One thing moves him at a time: not while the pointer has him peeking out, a drag, or a ride-along.
    static func mayStepAside(peeking: Bool, dragging: Bool, following: Bool) -> Bool { !peeking && !dragging && !following }

    /// While Maxie works (thinking), a free Maxie strolls beside his spot; back home when he's done.
    private func working(_ on: Bool) {
        guard on else { return stroll.stop(home: botFrame) }
        guard DebugSnapshot.dir == nil, let mini, mode == "free", !placement.isAtNotch, !placement.isTucked, stepped == .stay,
              !(drag?.isDragging ?? false), !following, !stroll.isWalking else { return }
        let blocked = StepAside.frontWindows() + [big?.frame].compactMap { $0 }
        stroll.start(mini, spots: Alive.walk(home: botFrame, screen: screen.visibleFrame, blocked: blocked,
                                             accepted: acceptedCover, reduce: Motion.reduce))
    }

    private static func fullscreenWindowPresent(_ list: [WindowList.Window]) -> Bool {
        WindowList.fullscreen(list, screens: NSScreen.screens.map { $0.frame.size }, own: getpid())
    }

    // MARK: - The panel

    /// ⌥Space: open with the cursor in the box, or close when it is already open.
    func toggle() {
        big == nil ? expand(reason: .hotkey) : collapse()
    }

    /// Back over everything after System Settings.
    private func floatPanel() {
        guard let big, big.level != PanelRules.level(steppedAside: false) else { return }
        big.level = PanelRules.level(steppedAside: false)
        big.orderFrontRegardless()
    }

    func expand(reason: Reason) {
        if big != nil, reason != .hover { floatPanel() }  // clicked while it was stepped aside: bring it back up
        guard big == nil, stepped != .hide || reason != .hover else { return }
        Log.d("expand \(reason)")
        openedBy = reason
        let s = screen
        let bot = mini?.frame ?? botFrame
        let layout = PanelLayout.frame(saved: DebugSnapshot.dir == nil ? PanelLayout.load(defaults) : .init(), bot: bot, screen: s.visibleFrame,
                                       screens: NSScreen.screens.map(\.visibleFrame), atNotch: placement.isAtNotch)
        let size = layout.frame.size
        let still = DebugSnapshot.dir != nil  // snapshots: no motion
        let source = layout.moved ? UnitPoint.top : UnitPoint(x: min(1, max(0, (bot.midX - layout.frame.minX) / size.width)), y: 0)  // the light comes from Maxie
        let panel = Self.makeChatPanel(still ? layout.frame : Motion.tuckFrame(panel: layout.frame, toward: bot))
        let view = PanelView(hub: hub, onClose: { [weak self] in self?.collapse(animated: true) },
                             onSettings: { [weak self] in self?.onSettings($0) },
                             onPin: { [weak self] in self?.setPinned($0) },
                             onQuit: { [weak self] in self?.onQuit() },
                             onResize: { [weak self] in self?.resizePanel(to: $0) },
                             onResizeEnd: { [weak self] in self?.savePanel() },
                             source: source)
        let host = PanelHostingView(rootView: view.frame(width: size.width).frame(maxHeight: .infinity))
        host.sizingOptions = []  // the panel's size is set here, not by the view's ideal size
        panel.contentView = host
        panel.alphaValue = still ? 1 : 0
        panel.orderFrontRegardless()
        panel.makeKey()  // typing goes to the box; Esc reaches the key monitor
        big = panel
        let remember: @MainActor () -> Void = { [weak self] in  // only the user's own moves are remembered, not the opening spring
            guard let self, self.big === panel else { return }
            self.placedPanel = layout.frame
            self.moveObserver = NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: panel, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.savePanel() }
            }
        }
        if still { remember() } else {
            Motion.animate(panel, to: layout.frame, alpha: 1, duration: Motion.duration(Motion.open), done: remember)
        }
        Task { await hub.refreshPanel() }
        setPinned(defaults.bool(forKey: PanelView.pinnedKey))
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in
            guard let key = PanelKey(e) else { return e }  // everything else goes to the panel
            MainActor.assumeIsolated { self?.perform(key) }
            return nil
        }
    }

    /// Esc closes, ⌘, opens Settings, ⌘Q asks, then quits.
    private func perform(_ key: PanelKey) {
        switch key {
        case .close: collapse(animated: true)
        case .settings: onSettings(nil)
        case .quit: onQuit()
        }
    }

    /// One rule: unpinned, a mouse-down anywhere outside the panel and Maxie closes it: in
    /// Settings or any other Maxie window (local monitor) and in other apps or the desktop (global monitor).
    func setPinned(_ pinned: Bool) {
        for m in [clickMonitor, localClickMonitor].compactMap({ $0 }) { NSEvent.removeMonitor(m) }
        clickMonitor = nil
        localClickMonitor = nil
        guard big != nil, !pinned else { return }
        let mask: NSEvent.EventTypeMask = [.leftMouseDown, .rightMouseDown]
        clickMonitor = NSEvent.addGlobalMonitorForEvents(matching: mask) { [weak self] _ in
            MainActor.assumeIsolated { self?.clickedElsewhere(at: NSEvent.mouseLocation) }
        }
        localClickMonitor = NSEvent.addLocalMonitorForEvents(matching: mask) { [weak self] e in
            let p = e.window.map { $0.convertPoint(toScreen: e.locationInWindow) } ?? NSEvent.mouseLocation
            MainActor.assumeIsolated { self?.clickedElsewhere(at: p) }
            return e
        }
    }

    private func clickedElsewhere(at p: CGPoint) {
        guard let big else { return }
        if PanelRules.closes(clickAt: p, panel: big.frame, bot: mini?.frame ?? .zero, pinned: panelPinned) { collapse(animated: true) }
    }

    /// The bottom edge was dragged: the top stays where it is.
    func resizePanel(to height: CGFloat) {
        guard let big else { return }
        big.setFrame(PanelLayout.resized(big.frame, to: height, in: (big.screen ?? screen).visibleFrame), display: true)
    }

    private func savePanel() {
        // snapshots never touch the user's settings; riding along with a dragged Maxie or set by Maxie isn't the user's move
        guard let big, DebugSnapshot.dir == nil, !following, PanelLayout.isUserMove(big.frame, placed: placedPanel) else { return }
        PanelLayout.save(big.frame, to: defaults)
    }

    /// Close the panel. Animated, it folds back into Maxie first (Esc, ×, a click elsewhere, another app).
    func collapse(animated: Bool = false) {
        Log.d("collapse (big: \(big != nil))")
        let closing = big
        big = nil
        openedBy = nil
        for m in [clickMonitor, localClickMonitor, keyMonitor].compactMap({ $0 }) { NSEvent.removeMonitor(m) }
        localClickMonitor = nil
        if let o = moveObserver { NotificationCenter.default.removeObserver(o) }
        clickMonitor = nil
        keyMonitor = nil
        moveObserver = nil
        if placement.isTucked, peeking, !hoverGate.inside {
            peeking = false
            slide()
        }
        guard let closing else { return }
        guard animated, DebugSnapshot.dir == nil else { return Self.discard(closing) }  // the web view stops with it
        let bot = mini?.frame ?? botFrame
        closing.ignoresMouseEvents = true
        Motion.animate(closing, to: Motion.tuckFrame(panel: closing.frame, toward: bot), alpha: 0,
                       duration: Motion.duration(Motion.tuck), curve: Motion.away) { Self.discard(closing) }
    }

    /// The chat: takes typing and Esc, without activating Maxie.
    static func makeChatPanel(_ rect: CGRect) -> NSPanel {
        configure(KeyPanel(contentRect: rect, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false))
    }

    /// Maxie himself: mouse only. When the chat closes, AppKit makes the next window of this app that can be key
    /// the key one; if that were Maxie himself, the user's typing would keep going to Maxie.
    static func makeBotWindow(_ rect: CGRect) -> NSPanel {
        configure(BotPanel(contentRect: rect, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false))
    }

    private static func configure(_ p: NSPanel) -> NSPanel {
        p.isFloatingPanel = true
        p.level = .statusBar
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = false
        p.hidesOnDeactivate = false
        p.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        p.isReleasedWhenClosed = false  // ARC owns it; discard() closes it
        return p
    }

    /// Done with a bot or panel window: take its views out (their timers and web views stop) and close it.
    /// Hiding alone left every old copy alive, and some back on screen (61 stacked at the notch once).
    static func discard(_ window: NSWindow?) {
        guard let window else { return }
        window.childWindows?.forEach { window.removeChildWindow($0) }  // an open panel following him stays
        window.orderOut(nil)
        window.contentView = nil
        window.close()
    }
}

/// Maxie's own window (next to the notch or free): clicks, hover and drags, never the keyboard.
final class BotPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// A borderless panel that can take key events (for Esc and typing) without activating the app.
final class KeyPanel: NSPanel {
    override var canBecomeKey: Bool { true }

    /// ⌘C and friends go straight to the selected text: Maxie is usually not the active app, so they can't count on
    /// its Edit menu.
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if let action = EditKeys.action(for: event), firstResponder?.tryToPerform(action, with: self) == true { return true }
        return super.performKeyEquivalent(with: event)
    }
}

/// The standard edit keys and the action each one sends along the responder chain.
enum EditKeys {
    static func action(for event: NSEvent) -> Selector? {
        let mods = event.modifierFlags.intersection([.command, .shift, .option, .control])
        guard mods == .command || mods == [.command, .shift] else { return nil }
        switch (event.charactersIgnoringModifiers?.lowercased(), mods.contains(.shift)) {
        case ("c", false): return #selector(NSText.copy(_:))
        case ("x", false): return #selector(NSText.cut(_:))
        case ("v", false): return #selector(NSText.paste(_:))
        case ("a", false): return #selector(NSResponder.selectAll(_:))
        case ("z", false): return Selector(("undo:"))
        case ("z", true): return Selector(("redo:"))
        default: return nil
        }
    }
}

/// Maxie's window content: hover, click, double-click and drag (click-hold then move) handled here so
/// only Maxie itself is clickable and nothing around it changes.
final class DragView: NSView {
    var onHover: (Bool) -> Void = { _ in }
    var onDragStart: () -> Void = {}
    var onDrag: (CGPoint) -> Void = { _ in }
    var onDrop: () -> Void = {}
    var onClick: () -> Void = {}
    var onDoubleClick: () -> Void = {}
    var canDrag = true
    private var downAt: CGPoint?
    private var dragging = false
    var isDragging: Bool { dragging }
    private var tracking: NSTrackingArea?

    override var isFlipped: Bool { true }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking { removeTrackingArea(tracking) }
        let t = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self)
        addTrackingArea(t)
        tracking = t
    }

    override func hitTest(_ point: NSPoint) -> NSView? { bounds.contains(convert(point, from: superview)) ? self : nil }

    override func mouseEntered(with event: NSEvent) { onHover(true) }
    override func mouseExited(with event: NSEvent) { onHover(false) }

    override func mouseDown(with event: NSEvent) {
        downAt = NSEvent.mouseLocation
        dragging = false
        if event.clickCount == 2 { onDoubleClick() }
    }

    override func mouseDragged(with event: NSEvent) {
        guard canDrag, let d = downAt else { return }
        let p = NSEvent.mouseLocation
        if !dragging, hypot(p.x - d.x, p.y - d.y) > 4 {
            dragging = true
            onDragStart()
        }
        if dragging { onDrag(p) }
    }

    override func mouseUp(with event: NSEvent) {
        if dragging { onDrop() } else if event.clickCount == 1 { onClick() }
        downAt = nil
        dragging = false
    }

    /// While dragging: a small rock back and forth.
    func wiggle(_ on: Bool) {
        wantsLayer = true
        guard let layer else { return }
        layer.anchorPoint = CGPoint(x: 0.5, y: 0.5)
        layer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        layer.removeAnimation(forKey: "wiggle")
        guard on else { return }
        let a = CABasicAnimation(keyPath: "transform.rotation.z")
        a.fromValue = -0.12
        a.toValue = 0.12
        a.duration = 0.18
        a.autoreverses = true
        a.repeatCount = .infinity
        layer.add(a, forKey: "wiggle")
    }

    /// Dropped: a small bounce.
    func bounce() {
        wantsLayer = true
        guard let layer else { return }
        layer.anchorPoint = CGPoint(x: 0.5, y: 0.5)
        layer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        let a = CAKeyframeAnimation(keyPath: "transform.scale")
        a.values = [1, 1.14, 0.94, 1.04, 1]
        a.keyTimes = [0, 0.3, 0.6, 0.8, 1]
        a.duration = 0.4
        layer.add(a, forKey: "bounce")
    }
}

/// Maxie itself. "listen" from the hub opens the panel.
struct MiniBot: View {
    let hub: HubClient
    var scale: CGFloat = 1
    let onListen: () -> Void
    var onWork: (Bool) -> Void = { _ in }
    var onNotice: (VoiceNotice) -> Void = { _ in }

    /// What the notch shows: the hub's state as it is. His eyes close only when the user said "stop
    /// watching" (the hub's base) or is asleep, never because the mic is paused; asleep while the hub is offline.
    static func face(connected: Bool, state: BotState) -> BotState { connected ? state : .sleep }

    var body: some View {
        let face = Self.face(connected: hub.connected, state: hub.state)
        MascotLayer(state: face, scale: scale)
            .frame(width: NotchController.notchSize.width * scale, height: NotchController.notchSize.height * scale)
            .accessibilityLabel("Maxie, \(hub.connected ? face.label : "hub offline")")
            .onChange(of: hub.state) { _, s in
                if s == .listen { onListen() }
                onWork(s == .think)
            }
            .onChange(of: hub.notice) { _, n in if let n { onNotice(n) } }
            .help(hub.connected ? "Maxie: \(hub.state.label)" : "Maxie: hub offline")
    }
}
