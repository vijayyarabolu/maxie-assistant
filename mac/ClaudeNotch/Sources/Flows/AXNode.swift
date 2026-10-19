import AppKit
import ApplicationServices

/// A control in another app, read through Accessibility (the Maxie app needs that permission).
struct AXNode: FlowNode {
    let element: AXUIElement

    /// Controls a click means; a click on the text or picture inside one is a click on it.
    static let actionable: Set<String> = ["AXButton", "AXLink", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton",
                                          "AXPopUpButton", "AXMenuButton", "AXTextField", "AXTextArea", "AXComboBox",
                                          "AXSearchField", "AXCell", "AXRow", "AXTab", "AXDisclosureTriangle", "AXSlider"]

    private func value(_ name: String) -> CFTypeRef? {
        var v: CFTypeRef?
        return AXUIElementCopyAttributeValue(element, name as CFString, &v) == .success ? v : nil
    }
    private func string(_ name: String) -> String { (value(name) as? String) ?? "" }
    private func node(_ name: String) -> AXNode? {
        guard let v = value(name), CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return AXNode(element: unsafeBitCast(v, to: AXUIElement.self))
    }

    var role: String { string(kAXRoleAttribute) }
    var subrole: String { string(kAXSubroleAttribute) }
    var title: String { string(kAXTitleAttribute) }
    var label: String {
        let d = string(kAXDescriptionAttribute)
        return d.isEmpty ? string("AXPlaceholderValue") : d
    }
    var identifier: String { string("AXIdentifier") }
    var value: String { (value(kAXValueAttribute) as? String) ?? "" }
    var children: [AXNode] {
        guard let arr = value(kAXChildrenAttribute) as? [AXUIElement] else { return [] }
        return arr.map(AXNode.init(element:))
    }
    var parent: AXNode? { node(kAXParentAttribute) }
    var window: AXNode? { node(kAXWindowAttribute) }
    var windows: [AXNode] { (value(kAXWindowsAttribute) as? [AXUIElement])?.map(AXNode.init(element:)) ?? [] }
    var isSecure: Bool { subrole == (kAXSecureTextFieldSubrole as String) }
    var pid: pid_t {
        var p: pid_t = 0
        AXUIElementGetPid(element, &p)
        return p
    }

    var frame: CGRect? {
        guard let p = value(kAXPositionAttribute), let s = value(kAXSizeAttribute),
              CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
        var point = CGPoint.zero, size = CGSize.zero
        AXValueGetValue(unsafeBitCast(p, to: AXValue.self), .cgPoint, &point)
        AXValueGetValue(unsafeBitCast(s, to: AXValue.self), .cgSize, &size)
        return CGRect(origin: point, size: size)
    }

    /// The nearest control a click means (itself, or a parent within three levels).
    var target: AXNode {
        var n: AXNode? = self
        for _ in 0..<4 {
            guard let cur = n else { break }
            if Self.actionable.contains(cur.role) { return cur }
            n = cur.parent
        }
        return self
    }

    func press() -> Bool {
        AXUIElementPerformAction(element, kAXPressAction as CFString) == .success
    }

    func focus() {
        AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    }

    static func app(_ pid: pid_t) -> AXNode { AXNode(element: AXUIElementCreateApplication(pid)) }

    /// The control under a screen point in AppKit coordinates (origin at the bottom left of the main screen).
    static func at(appKitPoint p: CGPoint) -> AXNode? {
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        var el: AXUIElement?
        let ok = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(p.x), Float(top - p.y), &el)
        return ok == .success ? el.map(AXNode.init(element:)) : nil
    }

    static func focused() -> AXNode? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), kAXFocusedUIElementAttribute as CFString, &v) == .success,
              let v, CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return AXNode(element: unsafeBitCast(v, to: AXUIElement.self))
    }

    /// Asks macOS for the Accessibility permission (shows the system prompt once).
    static func trusted(prompt: Bool) -> Bool {
        let key = "AXTrustedCheckOptionPrompt"  // kAXTrustedCheckOptionPrompt (a global var Swift 6 flags)
        return AXIsProcessTrustedWithOptions([key: prompt] as CFDictionary)
    }
}
