import AppKit
import Darwin

/// One click opens Maxie (it took 4-5). A launch put only the small Maxie at the notch: a menu-bar app
/// has no window, so it looked like nothing happened and the user clicked again. Now a launch they made shows the panel, a
/// second copy hands over to the running one and quits, and debug builds log how long a launch took to show.
enum LaunchRules {
    static let waitTries = 10
    static let waitPause: TimeInterval = 0.15

    /// Dock, Finder, Spotlight, `open`: show the panel. At login nobody is looking; a file or URL open has its own job.
    static func opensPanel(isDefaultLaunch: Bool, asLoginItem: Bool) -> Bool { isDefaultLaunch && !asLoginItem }

    static func handsOff(others: [pid_t]) -> Bool { !others.isEmpty }

    /// True when another Maxie is still there after a short wait. One that is quitting (macOS's "Quit & Reopen" after
    /// a permission change) must not swallow this launch.
    static func waitForOthers(_ others: () -> [pid_t], tries: Int = waitTries, pause: TimeInterval = waitPause) -> Bool {
        for _ in 0..<tries {
            if others().isEmpty { return false }
            if pause > 0 { Thread.sleep(forTimeInterval: pause) }
        }
        return !others().isEmpty
    }

    /// Other running copies of this app (same bundle id), not the one launching.
    static func otherCopies() -> [pid_t] {
        NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? "")
            .filter { $0.processIdentifier != getpid() && !$0.isTerminated }
            .map(\.processIdentifier)
    }

    /// Started by "Open at Login" (Settings › General), read from the launch's Apple event.
    static func launchedAsLoginItem() -> Bool {
        guard let e = NSAppleEventManager.shared().currentAppleEvent, e.eventID == kAEOpenApplication else { return false }
        return e.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
    }

    /// When this process started, from the kernel.
    static func processStart(pid: pid_t = getpid()) -> Date? {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
        guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0, size > 0 else { return nil }
        let t = info.kp_proc.p_starttime
        return Date(timeIntervalSince1970: Double(t.tv_sec) + Double(t.tv_usec) / 1_000_000)
    }

    /// Debug builds: "launch → visible: 640 ms" in the stall log, once Maxie (and the panel) drew their first frame.
    static func logVisible() {
        guard let start = processStart() else { return }
        DispatchQueue.main.async {
            MainThreadWatchdog.append(line: "launch → visible: \(Int(Date().timeIntervalSince(start) * 1000)) ms")
        }
    }
}
