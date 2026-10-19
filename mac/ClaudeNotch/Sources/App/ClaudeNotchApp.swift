import SwiftUI

/// Maxie in the notch: a menu-bar app with a tiny live bot next to the notch that opens into a panel.
/// The Python hub (127.0.0.1:8780) is the brain; this app only shows its state and sends your answers back.
@main
struct ClaudeNotchApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        // No menu bar item while rendering screenshots off-screen (DebugSnapshot): nothing the user can see.
        MenuBarExtra(isInserted: .constant(DebugSnapshot.dir == nil && ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil)) {
            MenuContent(hub: delegate.hub, delegate: delegate)
        } label: {
            // A small bot, always in the menu bar, so you can always find Maxie.
            Image(nsImage: BotGlyph.image(asleep: !delegate.hub.connected, muted: delegate.hub.voiceMuted,
                                          classic: delegate.hub.maxie?.isClassic ?? (MaxieLook.character() == .classic)))
                .accessibilityLabel(delegate.hub.connected ? "Maxie: \(delegate.hub.state.label)" : "Maxie: Mac hub offline")
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let hub = HubClient()
    lazy var notch = NotchController(hub: hub)
    lazy var settings = SettingsWindow(hub: hub, notch: notch)
    lazy var flows = FlowCoordinator(hub: hub, notch: notch)
    private let voice = VoiceHooks()
    /// Messages, Calendar, Contacts, Notes, Mail, Spotlight and the on-device model for the hub.
    private var bridge: BridgeServer?
    private let hotKeys = HotKeys()
    /// Set when Quit muted the voice helper ("Voice and Maxie stop"); the next launch unmutes it.
    static let mutedByQuitKey = "voiceMutedByQuit"
    #if DEBUG
    private lazy var watchdog = MainThreadWatchdog()  // logs main-thread stalls with their stack
    #endif

    private var asLoginItem = false

    /// A second copy hands over to the running Maxie and quits quietly. Never the tests' own copy
    /// or an off-screen render, which run beside the installed Maxie on purpose.
    func applicationWillFinishLaunching(_ notification: Notification) {
        guard ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil, DebugSnapshot.dir == nil else { return }
        asLoginItem = LaunchRules.launchedAsLoginItem()
        if LaunchRules.handsOff(others: LaunchRules.otherCopies()), LaunchRules.waitForOthers(LaunchRules.otherCopies) {
            DistributedNotificationCenter.default().postNotificationName(NotchController.remoteName, object: "open",
                                                                         deliverImmediately: true)
            exit(0)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil {  // unit tests: no UI at all
            NSApp.setActivationPolicy(.prohibited)
            return
        }
        hub.start()
        notch.onSettings = { [weak self] in self?.settings.show(section: $0) }  // the panel self-test presses ⌘, too
        notch.onSettingsClose = { [weak self] in self?.settings.close() }
        if DebugSnapshot.dir != nil {  // off-screen renders only: no notch, hotkeys, voice or flows
            DebugSnapshot.run(notch: notch, hub: hub)
            return
        }
        #if DEBUG
        watchdog.start()  // the installed Maxie only: never the tests' or the off-screen renders'
        #endif
        if let token = Keychain.read("HUB_TOKEN") {
            bridge = BridgeServer(token: token, handle: BridgeRoutes.handle)
            bridge?.start()
        }
        Task.detached(priority: .background) { await MemoryIndex.refresh() }  // Siri / Spotlight see the user's memory
        notch.show()
        let isDefaultLaunch = notification.userInfo?[NSApplication.launchIsDefaultUserInfoKey] as? Bool ?? false
        if LaunchRules.opensPanel(isDefaultLaunch: isDefaultLaunch, asLoginItem: asLoginItem) {
            notch.expand(reason: .menu)  // one click: the user sees Maxie answer at once
        }
        #if DEBUG
        LaunchRules.logVisible()
        #endif
        notch.onQuit = { [weak self] in self?.quitAsking() }
        LightWindow.onQuit = { [weak self] in self?.quitAsking() }
        hotKeys.register(HotKeys.optionSpace, id: 1) { [weak self] in self?.notch.toggle() }
        hotKeys.register(HotKeys.optionShiftS, id: 2) { [weak self] in
            self?.notch.expand(reason: .hotkey)
            self?.hub.lookAtScreen(question: "What's on my screen?")
        }
        hotKeys.register(HotKeys.optionShiftW, id: 3) { [weak self] in self?.flows.toggle() }
        _ = flows  // listens for "watch me" and approved replays from the hub
        voice.onWake = { [weak self] in
            self?.hub.post(state: .listen)
            PresenceReporter.shared.active("voice")  // "Hey Max": the user is up
            self?.notch.expand(reason: .wake)
        }
        voice.start()
        Task { await LocationFollower.shared.attach(hub) }  // About me follows the user (significant changes only)
        PresenceReporter.shared.attach(hub)  // Maxie sleeps when the user sleeps: lock, displays, idle, media
        Observer.shared.attach(hub)  // watch and learn: app switches and safe window titles
        if UserDefaults.standard.bool(forKey: Self.mutedByQuitKey) {
            UserDefaults.standard.removeObject(forKey: Self.mutedByQuitKey)
            hub.setMuted(false)  // back from Quit: listen again
        }
        FlowSelfTest.runIfAsked()
    }

    /// A Dock click or `open` on Maxie while he runs: the panel comes up. No Dock icon of his own
    /// (LSUIElement), but a pinned one or Spotlight still lands here.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard DebugSnapshot.dir == nil, ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil else { return false }
        notch.expand(reason: .menu)
        return false
    }

    /// ⌘Q, More › Quit, the menu bar: ask, then mute the voice helper (it is a separate service and
    /// would keep hearing "Hey Max") and quit.
    func quitAsking() {
        let a = NSAlert()
        a.messageText = "Quit Maxie?"
        a.informativeText = "Voice and Maxie stop. Open Maxie again to bring them back."
        a.addButton(withTitle: "Quit")
        a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        Task {
            if !hub.voiceMuted {
                UserDefaults.standard.set(true, forKey: Self.mutedByQuitKey)
                await hub.setMutedNow(true)
            }
            NSApp.terminate(nil)
        }
    }
}

struct MenuContent: View {
    let hub: HubClient
    let delegate: AppDelegate

    var body: some View {
        Text(hub.connected ? "Maxie is \(hub.state.label.lowercased())" : "Hub offline. Is the Mac service running?")
        Divider()
        Button("Open  \(HotKeys.optionSpace.label)") { delegate.notch.expand(reason: .menu) }
        Button("Settings…") { delegate.settings.show() }
            .keyboardShortcut(",", modifiers: [.command])
        Button(hub.voiceMuted ? "Resume listening" : "Pause listening") { hub.setMuted(!hub.voiceMuted) }
        Toggle("Watch and learn", isOn: Binding(get: { hub.eyesOpen }, set: { hub.setWatching($0) }))  // his eyes
        Divider()
        Button("Look at my screen  \(HotKeys.optionShiftS.label)") {
            delegate.notch.expand(reason: .menu)
            hub.lookAtScreen(question: "What's on my screen?")
        }
        Button(hub.watching ? "Stop recording  \(HotKeys.optionShiftW.label)" : "Watch me once  \(HotKeys.optionShiftW.label)") {
            hub.onToggleWatch?()
        }
        Button("Stop current task") { hub.stop() }
            .keyboardShortcut(".", modifiers: [.command])
        Divider()
        Button("Quit Maxie…") { delegate.quitAsking() }
            .keyboardShortcut("q", modifiers: [.command])
    }
}
