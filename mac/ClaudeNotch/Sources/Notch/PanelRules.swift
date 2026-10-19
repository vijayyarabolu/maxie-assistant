import AppKit

/// The panel's rules that don't need a window, kept pure so tests can pin them down.
enum PanelRules {
    /// One rule: a mouse-down anywhere outside the panel and outside Maxie closes an unpinned panel
    /// (clicks in Settings, on the desktop, in other apps). Pinned stays; × and Esc still close it.
    static func closes(clickAt p: CGPoint, panel: CGRect, bot: CGRect, pinned: Bool) -> Bool {
        !pinned && !panel.contains(p) && !bot.contains(p)
    }

    /// While Maxie is dragged, a panel the user opened on purpose (or pinned) rides along; one a hover opened
    /// and nothing pinned simply closes, as before.
    static func follows(openedByHover: Bool, pinned: Bool) -> Bool {
        pinned || !openedByHover
    }

    /// The user switched to another app (⌘Tab, the Dock, a click): an unpinned panel tucks away, so it
    /// never sits over what the user is working on. Maxie's own Settings window is not another app.
    static func tucks(activated pid: pid_t?, own: pid_t, pinned: Bool) -> Bool {
        guard let pid else { return false }
        return !pinned && pid != own
    }

    /// The chat floats over everything, except while System Settings is in front for the user.
    static func level(steppedAside: Bool) -> NSWindow.Level { steppedAside ? .normal : .statusBar }
}
