import Foundation
import CoreGraphics

/// Which character the app shows: Maxie (Mochi, the default) or the Classic robot, and where a
/// customized Maxie's loops live once the hub has re-rendered them (Settings › Maxie). Kept in UserDefaults so
/// the right bot is on screen before the hub answers.
enum MaxieLook {
    enum Character: String, CaseIterable, Sendable { case maxie, classic }

    static let characterKey = "maxie.character"
    static let loopsKey = "maxie.loopsDir"
    static let sizeKey = "maxie.size"
    /// The free-floating bot's scale per Settings › Maxie › Size (the notch has one size: 1).
    static let freeScales: [String: CGFloat] = ["small": 1.25, "medium": 1.5, "large": 1.8]

    static func freeScale(_ d: UserDefaults = .standard) -> CGFloat {
        freeScales[d.string(forKey: sizeKey) ?? ""] ?? 1.5
    }

    static func character(_ d: UserDefaults = .standard) -> Character {
        Character(rawValue: d.string(forKey: characterKey) ?? "") ?? .maxie
    }

    /// Where the loops rendered for a custom look are; nil when there are none: the bundled ones. From the
    /// settings only: the folder may be in ~/Documents, and the first look at it can wait on macOS asking the user for
    /// access, so MascotLibrary reads it in the background and falls back if it's incomplete.
    static func customLoops(_ d: UserDefaults = .standard) -> URL? {
        guard readsUserFolders(d), let path = d.string(forKey: loopsKey), !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    static let isUnitTest = ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil

    /// The unit-test host is Maxie.app itself and shares the user's real defaults (same bundle id). The rendered look may live
    /// in ~/Documents, and a test build reading it is a new app to macOS: it asked the user for access (never
    /// disturb). Tests then get the bundled look; a test's own defaults still work.
    static func readsUserFolders(_ d: UserDefaults, isUnitTest: Bool = isUnitTest) -> Bool {
        !(isUnitTest && d === UserDefaults.standard)
    }

    /// The folder the loops come from: the custom look if there is one.
    static func loopsRoot(bundle: URL? = Bundle.main.resourceURL, _ d: UserDefaults = .standard) -> URL? {
        character(d) == .maxie ? customLoops(d) ?? bundledRoot(bundle: bundle, d) : bundledRoot(bundle: bundle, d)
    }

    /// The character's loops as the app ships them, inside its own bundle (never asks macOS for anything).
    static func bundledRoot(bundle: URL? = Bundle.main.resourceURL, _ d: UserDefaults = .standard) -> URL? {
        bundle?.appending(path: character(d) == .classic ? "mascot-classic" : "mascot")
    }

    /// Stores what the hub says; true when anything changed (Maxie then reloads).
    @discardableResult
    static func apply(character: String, loopsDir: String?, size: String = "medium", _ d: UserDefaults = .standard) -> Bool {
        let changed = d.string(forKey: characterKey) != character || d.string(forKey: loopsKey) != loopsDir
            || (d.string(forKey: sizeKey) ?? "medium") != size
        d.set(character, forKey: characterKey)
        d.set(loopsDir, forKey: loopsKey)
        d.set(size, forKey: sizeKey)
        return changed
    }
}

extension Notification.Name {
    /// The character or its loops changed: views showing Maxie reload their frames.
    static let mascotChanged = Notification.Name("maxie.mascotChanged")
}
