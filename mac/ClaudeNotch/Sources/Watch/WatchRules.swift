import Foundation

/// What may leave the app for the hub when the user switches apps or a window title changes. The hub
/// checks everything again (maxie.watch.admit); these rules make sure the worst never leaves this process.
enum WatchRules {
    static let messages = "com.apple.MobileSMS"
    static let maxText = 120

    /// Banking, health and password apps: not even the fact that the user opened one.
    static let neverWords = ["passwords", "keychainaccess", "com.apple.health", "1password", "agilebits", "bitwarden",
                             "lastpass", "dashlane", "keeper", "chase", "bankofamerica", "wellsfargo", "capitalone",
                             "americanexpress", "amex", "schwab", "fidelity", "vanguard", "robinhood", "coinbase",
                             "venmo", "paypal", "zelle", "creditkarma", "bank", "health", "mychart", "medical",
                             "pharmacy"]
    static let neverCategories: Set<String> = ["public.app-category.finance", "public.app-category.healthcare-fitness",
                                               "public.app-category.medical"]
    /// A private window can't be told apart from a normal one, so no page title from any browser ever leaves.
    static let browserPrefixes = ["com.apple.Safari", "com.google.Chrome", "org.mozilla.", "company.thebrowser.",
                                  "com.microsoft.edgemac", "com.brave.Browser", "com.operasoftware.", "com.vivaldi.",
                                  "com.kagi."]
    static let sensitiveTitle = try! NSRegularExpression(  // swiftlint:disable:this force_try
        pattern: #"private (browsing|window)|incognito|inprivate|password|passcode|verification|one-time|\botp\b|sign[ -]?in|log[ -]?in|\bbank|credit card|card ending|paypal|venmo|mychart|patient|pharmacy|prescription|therapy|\bhealth|medical|\d{4,}"#,
        options: [.caseInsensitive])

    static func neverApp(bundle: String, name: String, category: String?) -> Bool {
        if let category, neverCategories.contains(category) { return true }
        let words = (bundle + " " + name).lowercased()
        return neverWords.contains { words.contains($0) }
    }

    /// Chrome web apps (com.google.Chrome.app.…) are apps of their own, with their own names: those are fine.
    static func isBrowser(_ bundle: String) -> Bool {
        !bundle.contains(".app.") && browserPrefixes.contains { bundle.hasPrefix($0) }
    }

    static func sensitive(_ title: String) -> Bool {
        sensitiveTitle.firstMatch(in: title, range: NSRange(title.startIndex..., in: title)) != nil
    }

    static func excluded(bundle: String, name: String, list: [String]) -> Bool {
        let lowered = Set(list.map { $0.lowercased() })
        return lowered.contains(bundle.lowercased()) || lowered.contains(name.lowercased())
    }

    /// The items for one moment: the user switched to an app (switched: true) and/or its window title is this.
    static func items(bundle: String, name: String, category: String?, title: String?, secure: Bool,
                      switched: Bool, excluded list: [String]) -> [[String: Any]] {
        guard !bundle.isEmpty, !neverApp(bundle: bundle, name: name, category: category),
              !excluded(bundle: bundle, name: name, list: list) else { return [] }
        var out: [[String: Any]] = []
        if switched { out.append(["kind": "app", "app": bundle, "name": name, "category": category ?? ""]) }
        let clean = (title ?? "").split(whereSeparator: \.isWhitespace).joined(separator: " ")
        guard !clean.isEmpty, !secure, !isBrowser(bundle), !sensitive(clean) else { return out }
        out.append(["kind": bundle == messages ? "message" : "title", "app": bundle, "name": name,
                    "detail": String(clean.prefix(maxText))])
        return out
    }
}
