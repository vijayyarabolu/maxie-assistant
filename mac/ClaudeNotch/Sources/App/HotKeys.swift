import AppKit
import Carbon

/// Global hotkeys through Carbon's RegisterEventHotKey: they work without the Accessibility permission.
/// ⌥Space opens the panel with the cursor in the box, ⌥⇧S looks at the screen.
@MainActor
final class HotKeys {
    struct Key: Sendable {
        let code: UInt32
        let modifiers: UInt32
        var label: String
    }

    static let optionSpace = Key(code: UInt32(kVK_Space), modifiers: UInt32(optionKey), label: "⌥Space")
    static let optionShiftS = Key(code: UInt32(kVK_ANSI_S), modifiers: UInt32(optionKey | shiftKey), label: "⌥⇧S")
    static let optionShiftW = Key(code: UInt32(kVK_ANSI_W), modifiers: UInt32(optionKey | shiftKey), label: "⌥⇧W")

    nonisolated(unsafe) private static var handlers: [UInt32: @MainActor @Sendable () -> Void] = [:]
    private var refs: [EventHotKeyRef] = []
    private var handlerRef: EventHandlerRef?

    func register(_ key: Key, id: UInt32, _ handler: @escaping @MainActor @Sendable () -> Void) {
        installOnce()
        var ref: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: 0x434C_4155 /* CLAU */, id: id)
        let status = RegisterEventHotKey(key.code, key.modifiers, hotKeyID, GetApplicationEventTarget(), 0, &ref)
        guard status == noErr, let ref else {
            Log.d("hotkey \(key.label) not registered: \(status)")
            return
        }
        refs.append(ref)
        Self.handlers[id] = handler
    }

    private func installOnce() {
        guard handlerRef == nil else { return }
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var hotKeyID = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            let id = hotKeyID.id
            Task { @MainActor in HotKeys.handlers[id]?() }
            return noErr
        }, 1, &spec, nil, &handlerRef)
    }
}
