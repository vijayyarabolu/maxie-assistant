import SwiftUI
import AppKit
import Foundation

/// Small things the Settings window does on the Mac itself.
enum MacHelpers {
    static let hubLabel = "dev.maxie.hub"
    static let voiceLabel = "dev.maxie.voice"
    static let logsFolder = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/maxie")

    /// Restart a launchd agent (after a key change, a new mic, a Google login).
    static func kickstart(_ label: String) {
        run("/bin/launchctl", ["kickstart", "-k", "gui/\(getuid())/\(label)"])
    }

    /// Store a secret under service maxie. The value never goes through a log.
    static func keychainWrite(account: String, value: String) -> Bool {
        run("/usr/bin/security", ["add-generic-password", "-U", "-s", "maxie", "-a", account, "-w", value]) == 0
    }

    @discardableResult
    static func run(_ path: String, _ args: [String]) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return -1 }
        p.waitUntilExit()
        return p.terminationStatus
    }

    /// His OK before Maxie touches something outside itself (a token file, the tailnet). True = go ahead.
    static func confirm(_ title: String, _ text: String, ok: String) -> Bool {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.addButton(withTitle: ok)
        a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        return a.runModal() == .alertFirstButtonReturn
    }

    /// Quit and start this Maxie again (a Screen Recording switch only applies to a fresh launch).
    /// Not Quit's path: voice keeps listening. A detached shell waits for the quit, then opens the app.
    static func reopen(bundle: URL = Bundle.main.bundleURL) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = reopenArguments(bundle: bundle)
        do { try p.run() } catch { return }  // couldn't schedule the relaunch: stay open
        NSApp.terminate(nil)
    }

    /// The app's path is the script's $0, never pasted into it.
    static func reopenArguments(bundle: URL) -> [String] {
        ["-c", "sleep 1; /usr/bin/open \"$0\"", bundle.path]
    }

    static let systemSettingsID = "com.apple.systempreferences"

    /// System Settings on that pane, in front, in one click. Since macOS 14 an app opened by another
    /// only comes forward when the opener yields to it, so it stayed behind; and Maxie's floating chat steps aside.
    static func openSystemSettings(_ pane: String,
                                   open: (URL, NSWorkspace.OpenConfiguration) -> Void = { NSWorkspace.shared.open($0, configuration: $1) }) {
        guard let url = URL(string: "x-apple.systempreferences:" + pane) else { return }
        NSApp.yieldActivation(toApplicationWithBundleIdentifier: systemSettingsID)
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        NotificationCenter.default.post(name: .maxieStepsAside, object: nil)
        open(url, config)
    }

    static func openLogs() {
        try? FileManager.default.createDirectory(at: logsFolder, withIntermediateDirectories: true)
        NSWorkspace.shared.open(logsFolder)
    }
}


extension Binding {
    /// A binding that runs `action` only when the user changes the value (a control's setter), never when
    /// the view loads it. `.onChange` fires on loads too: opening Settings › Voice used to rewrite the
    /// voice setting and restart the voice helper.
    func onSet(_ action: @escaping (Value) -> Void) -> Binding<Value> {
        Binding(get: { wrappedValue }, set: { newValue in
            wrappedValue = newValue
            action(newValue)
        })
    }
}

extension Notification.Name {
    /// System Settings is coming to the front for the user: Maxie's floating chat drops below it.
    static let maxieStepsAside = Notification.Name("maxie.stepsAside")
    /// Settings › General › Quit Maxie: the app asks, stops his services and quits (AppDelegate.quitAsking).
    static let maxieQuits = Notification.Name("maxie.quits")
}
