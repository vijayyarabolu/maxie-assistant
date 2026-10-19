// Is the user away? Prints "<idle seconds> <full-screen app count> <locked or displays asleep: 1/0>" ("never disturb the user").
import AppKit
import CoreGraphics

let idle = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: CGEventType(rawValue: ~0)!)
let screens = NSScreen.screens.map { $0.frame.size }
let wins = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
let full = wins.filter { w in
    guard (w[kCGWindowLayer as String] as? Int) == 0, let b = w[kCGWindowBounds as String] as? [String: CGFloat] else { return false }
    return screens.contains { $0.width == b["Width"] && $0.height == b["Height"] }
}
let session = CGSessionCopyCurrentDictionary() as? [String: Any]
let dark = (session?["CGSSessionScreenIsLocked"] as? Bool ?? false) || CGDisplayIsAsleep(CGMainDisplayID()) != 0
print("\(Int(idle)) \(full.count) \(dark ? 1 : 0)")  // locked: nothing renders, and nothing new can read ~/Documents
