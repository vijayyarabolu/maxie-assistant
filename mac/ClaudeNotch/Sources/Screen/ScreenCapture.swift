import AppKit
import ScreenCaptureKit

/// One screenshot of the display under the mouse, only when asked (a button, ⌥⇧S, or "what's on my
/// screen"). ScreenCaptureKit, so the Screen Recording permission belongs to this app. The panel and the
/// bot are left out of the picture. Scaled to at most `maxSide` px and sent as a JPEG data URL.
enum ScreenCapture {
    static let maxSide: CGFloat = 1568

    static var hasPermission: Bool { CGPreflightScreenCaptureAccess() }

    @discardableResult
    static func requestPermission() -> Bool { CGRequestScreenCaptureAccess() }

    enum Failure: LocalizedError {
        case noPermission, noDisplay, encoding

        var errorDescription: String? {
            switch self {
            case .noPermission: "Screen Recording is off for Maxie. System Settings › Privacy & Security › Screen Recording."
            case .noDisplay: "No display to capture."
            case .encoding: "Couldn't encode the screenshot."
            }
        }
    }

    static func grab() async throws -> String {
        guard hasPermission else {
            requestPermission()
            throw Failure.noPermission
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let display = displayUnderMouse(content.displays) ?? content.displays.first
        guard let display else { throw Failure.noDisplay }
        let ours = content.windows.filter { $0.owningApplication?.processID == getpid() }
        let filter = SCContentFilter(display: display, excludingWindows: ours)
        let config = SCStreamConfiguration()
        let scale = min(1, maxSide / CGFloat(max(display.width, display.height)))
        config.width = Int(CGFloat(display.width) * scale)
        config.height = Int(CGFloat(display.height) * scale)
        config.showsCursor = false
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        guard let url = ImageEncoding.jpegDataURL(image, maxSide: maxSide) else { throw Failure.encoding }
        return url
    }

    private static func displayUnderMouse(_ displays: [SCDisplay]) -> SCDisplay? {
        let mouse = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first(where: { $0.frame.contains(mouse) }),
              let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID
        else { return nil }
        return displays.first { $0.displayID == number }
    }
}

enum ImageEncoding {
    /// "data:image/jpeg;base64,..." of the image, scaled so its long side is at most `maxSide`.
    static func jpegDataURL(_ image: CGImage, maxSide: CGFloat, quality: Double = 0.85) -> String? {
        let scale = min(1, maxSide / CGFloat(max(image.width, image.height)))
        let size = NSSize(width: CGFloat(image.width) * scale, height: CGFloat(image.height) * scale)
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width), pixelsHigh: Int(size.height),
                                   bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                   colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)
        guard let rep, let ctx = NSGraphicsContext(bitmapImageRep: rep) else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = ctx
        ctx.cgContext.interpolationQuality = .high
        ctx.cgContext.draw(image, in: CGRect(origin: .zero, size: size))
        NSGraphicsContext.restoreGraphicsState()
        guard let data = rep.representation(using: .jpeg, properties: [.compressionFactor: quality]) else { return nil }
        return "data:image/jpeg;base64," + data.base64EncodedString()
    }

    static func jpegDataURL(_ image: NSImage, maxSide: CGFloat) -> String? {
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        return jpegDataURL(cg, maxSide: maxSide)
    }
}
