import AppKit
import ScreenCaptureKit

/// Development aid for "readable over anything": with
/// CLAUDE_NOTCH_BACKDROPS="white=/a.png,terminal=/b.png,photo=/c.jpg" (and CLAUDE_NOTCH_SNAPSHOT=<dir>),
/// the app opens its real panel over a window showing each picture, captures exactly those two windows
/// with ScreenCaptureKit (so the system blur is real, and nothing else on the screen is in the picture),
/// in the look DebugSnapshot set for this run (dark, or light with CLAUDE_NOTCH_LIGHT=1: one look per
/// run, since switching the app's appearance mid-run leaves the panel dark), writes
/// panel-<look>-over-<name>.png, and quits.
@MainActor
enum BackdropShots {
    static let margin: CGFloat = 70

    static var backdrops: [(String, URL)] {
        (ProcessInfo.processInfo.environment["CLAUDE_NOTCH_BACKDROPS"] ?? "").split(separator: ",").compactMap {
            let kv = $0.split(separator: "=", maxSplits: 1).map(String.init)
            return kv.count == 2 ? (kv[0], URL(fileURLWithPath: kv[1])) : nil
        }
    }

    static func run(notch: NotchController, hub: HubClient, dir: URL) async {
        let look = ProcessInfo.processInfo.environment["CLAUDE_NOTCH_LIGHT"] != nil ? "light" : "dark"
        for (name, url) in backdrops {
            guard let image = NSImage(contentsOf: url) else { continue }
            for attempt in 1...3 {
                if await shoot(notch: notch, image: image, to: dir.appending(path: "panel-\(look)-over-\(name).png")) { break }
                print("backdrop \(look)/\(name): attempt \(attempt) missed")
            }
        }
    }

    /// One shot; false when the panel closed meanwhile (a Space change closes it) or the capture failed.
    private static func shoot(notch: NotchController, image: NSImage, to url: URL) async -> Bool {
        notch.expand(reason: .menu)
        try? await Task.sleep(for: .seconds(3))
        guard let panel = notch.panelWindow else { return false }
        let back = backdrop(around: panel.frame, image: image)
        defer { back.orderOut(nil); notch.collapse() }
        panel.order(.above, relativeTo: back.windowNumber)
        try? await Task.sleep(for: .seconds(1.5))
        for _ in 0..<10 {  // a just-shown window can be missing from the first shareable-content list
            guard notch.panelWindow != nil else { return false }
            if let shot = await capture([back, panel], rect: back.frame, screen: panel.screen ?? NSScreen.screens[0]) {
                write(shot, to: url)
                return true
            }
            try? await Task.sleep(for: .milliseconds(500))
        }
        return false
    }

    private static func backdrop(around frame: CGRect, image: NSImage) -> NSWindow {
        let rect = frame.insetBy(dx: -margin, dy: -margin)
        let w = NSWindow(contentRect: rect, styleMask: [.borderless], backing: .buffered, defer: false)
        w.level = .statusBar
        w.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        w.isOpaque = true
        w.isReleasedWhenClosed = false
        let v = NSImageView(frame: NSRect(origin: .zero, size: rect.size))
        v.image = image
        v.imageScaling = .scaleAxesIndependently
        w.contentView = v
        w.orderFrontRegardless()
        return w
    }

    static func capture(_ windows: [NSWindow], rect: CGRect, screen: NSScreen) async -> CGImage? {
        guard let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false) else { return nil }
        guard windows.allSatisfy({ $0.windowNumber > 0 }) else { return nil }
        let ids = Set(windows.map { CGWindowID($0.windowNumber) })
        let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID
        guard let display = content.displays.first(where: { $0.displayID == number }) ?? content.displays.first else { return nil }
        let windows = content.windows.filter { ids.contains($0.windowID) }
        guard windows.count == ids.count else {
            print("backdrop: \(windows.count) of \(ids.count) windows listed")
            return nil
        }
        let filter = SCContentFilter(display: display, including: windows)
        let config = SCStreamConfiguration()
        let scale = screen.backingScaleFactor
        // sourceRect is in the display's points, origin top-left.
        config.sourceRect = CGRect(x: rect.minX - screen.frame.minX, y: screen.frame.maxY - rect.maxY, width: rect.width, height: rect.height)
        config.width = Int(rect.width * scale)
        config.height = Int(rect.height * scale)
        config.showsCursor = false
        do {
            return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        } catch {
            print("backdrop: capture failed: \(error.localizedDescription)")
            return nil
        }
    }

    static func write(_ image: CGImage, to url: URL) {
        let rep = NSBitmapImageRep(cgImage: image)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}
