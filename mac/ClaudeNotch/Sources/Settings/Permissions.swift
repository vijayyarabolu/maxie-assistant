import AVFoundation
import AppKit
import ApplicationServices
import Contacts
import CoreLocation
import EventKit
import Security
import UserNotifications

/// Every macOS permission Maxie uses, asked from Maxie.app itself so Maxie shows up in each System Settings
/// list. Status is re-read every 2 s while Settings is open and on app activation, so flipping a
/// switch in System Settings shows at once. Prompts never come from the Python hub: TCC answers a child
/// process silently "no".
enum Permission: String, CaseIterable, Identifiable, Sendable {
    case microphone, notifications, accessibility, screen, calendars, reminders, contacts, location, fullDisk

    var id: String { rawValue }

    var title: String {
        switch self {
        case .microphone: "Microphone"
        case .notifications: "Notifications"
        case .accessibility: "Accessibility"
        case .screen: "Screen Recording"
        case .calendars: "Calendars"
        case .reminders: "Reminders"
        case .contacts: "Contacts"
        case .location: "Location"
        case .fullDisk: "Full Disk Access"
        }
    }

    var why: String {
        switch self {
        case .microphone: "\"Hey Max\". Nothing leaves the Mac before the wake word."
        case .notifications: "Briefs, alerts, things waiting for your OK."
        case .accessibility: "Watch-me-once flows, typing into other apps, learning your routines."
        case .screen: "\"What's on my screen\" (⌥⇧S), one screenshot when you ask."
        case .calendars: "Read and add events, on this Mac (free, no Google needed)."
        case .reminders: "Read and add reminders."
        case .contacts: "Names for \"text Riley\" and for who wrote to you."
        case .location: "Your city in About me, for weather and time. Never sent anywhere."
        case .fullDisk: "Optional. Only to search your Messages (\"the address Riley texted me\")."
        }
    }

    var pane: String {
        switch self {
        case .microphone: "com.apple.preference.security?Privacy_Microphone"
        case .notifications: "com.apple.Notifications-Settings.extension"
        case .accessibility: "com.apple.preference.security?Privacy_Accessibility"
        case .screen: "com.apple.preference.security?Privacy_ScreenCapture"
        case .calendars: "com.apple.preference.security?Privacy_Calendars"
        case .reminders: "com.apple.preference.security?Privacy_Reminders"
        case .contacts: "com.apple.preference.security?Privacy_Contacts"
        case .location: "com.apple.preference.security?Privacy_LocationServices"
        case .fullDisk: "com.apple.preference.security?Privacy_AllFiles"
        }
    }
}

/// Apps Maxie drives with Apple Events. macOS asks once per app, the first time Maxie sends one.
struct AutomationTarget: Identifiable, Sendable, Hashable {
    let name: String
    let bundleID: String
    var id: String { bundleID }

    static let all = [
        AutomationTarget(name: "Notes", bundleID: "com.apple.Notes"),
        AutomationTarget(name: "Reminders", bundleID: "com.apple.reminders"),
        AutomationTarget(name: "Messages", bundleID: "com.apple.MobileSMS"),
        AutomationTarget(name: "Mail", bundleID: "com.apple.mail"),
        AutomationTarget(name: "Music", bundleID: "com.apple.Music"),
    ]
}

enum Grant: Equatable, Sendable {
    case allowed, notAsked, denied
    case notRunning   // automation: the app isn't open, so macOS can't say yet
}

@MainActor
final class PermissionCenter: ObservableObject {
    @Published private(set) var grants: [Permission: Grant] = [:]
    @Published private(set) var automation: [AutomationTarget: AutomationStatus] = [:]
    @Published private(set) var stableSigning = PermissionCenter.isStablySigned()
    @Published private(set) var screen: ScreenAccess.Advice = .turnOn
    @Published var note = ""

    /// What Maxie sent the user to System Settings (or a later prompt) for; when its state changes, Settings comes back.
    private var waiting: Set<Permission> = []
    private var lastStates: [Permission: String] = [:]
    private let location = LocationAsker()
    private let memory = AutomationMemory()
    private static let askedAXKey = "askedAccessibility"
    private static let messagesDB = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Messages/chat.db")

    /// The user pressed "Turn on" for Accessibility before and it is still off: either they haven't flipped the switch,
    /// or they did for an older build (ad-hoc signing changed the code identity).
    var accessibilityLooksStale: Bool {
        grants[.accessibility] != .allowed && UserDefaults.standard.bool(forKey: Self.askedAXKey)
    }

    var onPlace: (@MainActor (LocationAsker.Place) -> Void)? {
        get { location.onPlace }
        set { location.onPlace = newValue }
    }

    // MARK: status

    func refresh() async {
        var g: [Permission: Grant] = [:]
        g[.microphone] = Self.av(AVCaptureDevice.authorizationStatus(for: .audio))
        g[.notifications] = Self.un(await UNUserNotificationCenter.current().notificationSettings().authorizationStatus)
        g[.accessibility] = AXIsProcessTrusted() ? .allowed : .notAsked
        g[.screen] = CGPreflightScreenCaptureAccess() ? .allowed : .notAsked
        g[.calendars] = Self.ek(EKEventStore.authorizationStatus(for: .event))
        g[.reminders] = Self.ek(EKEventStore.authorizationStatus(for: .reminder))
        g[.contacts] = Self.cn(CNContactStore.authorizationStatus(for: .contacts))
        g[.location] = location.grant
        g[.fullDisk] = FileHandle(forReadingAtPath: Self.messagesDB.path) != nil ? .allowed : .notAsked
        if g != grants { grants = g }
        let advice = await Self.screenAdvice(granted: g[.screen] == .allowed)
        if advice != screen { screen = advice }
        let states = Self.states(g, screen: advice)
        if !Self.answered(waiting: waiting, before: lastStates, after: states).isEmpty {
            waiting.removeAll()
            Self.comeBack()
        }
        lastStates = states
        var a: [AutomationTarget: AutomationStatus] = [:]
        for t in AutomationTarget.all { a[t] = memory.resolve(await Self.automationGrant(t, ask: false), for: t.bundleID) }
        if a != automation { automation = a }
    }

    // MARK: asking (the real macOS prompt, from this app)

    func turnOn(_ p: Permission) async {
        switch p {
        case .microphone:
            _ = await AVCaptureDevice.requestAccess(for: .audio)
            MacHelpers.kickstart(MacHelpers.voiceLabel)  // the voice helper asks for its own mic access next
        case .notifications:
            _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
        case .accessibility:
            UserDefaults.standard.set(true, forKey: Self.askedAXKey)
            let opts = ["AXTrustedCheckOptionPrompt": true] as CFDictionary  // kAXTrustedCheckOptionPrompt
            _ = AXIsProcessTrustedWithOptions(opts)
        case .screen:
            // macOS asks once per app identity; after that this returns false and nothing shows
            if !CGRequestScreenCaptureAccess() {
                UserDefaults.standard.set(true, forKey: ScreenAccess.sentKey)
                MacHelpers.openSystemSettings(p.pane)
                note = ScreenAccess.line
            }
        case .calendars:
            _ = try? await EKEventStore().requestFullAccessToEvents()
        case .reminders:
            _ = try? await EKEventStore().requestFullAccessToReminders()
        case .contacts:
            _ = try? await CNContactStore().requestAccess(for: .contacts)
        case .location:
            location.ask()
        case .fullDisk:
            MacHelpers.openSystemSettings(p.pane)  // macOS has no prompt for this one
        }
        if Self.asksInPlace(p) { Self.comeBack() } else { waiting.insert(p) }
        await refresh()
    }

    /// The user came back to Maxie on their own: nothing left to bring them back for.
    func cameBack() { waiting.removeAll() }

    /// macOS answers these with its own prompt, and the answer is in when the request returns. The rest go through
    /// System Settings (or a prompt that answers later): Maxie comes back once their state changes.
    static func asksInPlace(_ p: Permission) -> Bool {
        switch p {
        case .microphone, .notifications, .calendars, .reminders, .contacts: true
        case .accessibility, .screen, .fullDisk, .location: false
        }
    }

    /// The permissions Maxie is waiting on whose state changed between two looks.
    static func answered(waiting: Set<Permission>, before: [Permission: String], after: [Permission: String]) -> Set<Permission> {
        waiting.filter { before[$0] != after[$0] }
    }

    /// A prompt or a System Settings trip Maxie started is over: Settings comes back to the front, on the same page.
    /// macOS gives the keyboard to the last regular app instead (Claude, say), never to a menu-bar app.
    static func comeBack() {
        NotificationCenter.default.post(name: .maxieComesBack, object: nil)
    }

    private static func states(_ g: [Permission: Grant], screen: ScreenAccess.Advice) -> [Permission: String] {
        var out = g.mapValues { "\($0)" }
        out[.screen] = "\(g[.screen] ?? .notAsked) \(screen)"  // "on, reopen Maxie" counts as an answer too
        return out
    }

    func turnOn(_ t: AutomationTarget) async {
        note = "Asking macOS about \(t.name)…"
        let grant = await Self.automationGrant(t, ask: true, launch: true)
        memory.record(grant, for: t.bundleID)
        Self.comeBack()  // macOS's "Maxie wants to control Notes" prompt took the keyboard
        note = grant == .allowed ? "\(t.name): allowed." : grant == .denied ? "\(t.name) is off. Turn Maxie on under \(t.name) in System Settings." : ""
        await refresh()
    }

    // MARK: helpers

    /// Reads macOS's list off the main thread (a database read); forgets the System Settings trip once it works.
    private static func screenAdvice(granted: Bool) async -> ScreenAccess.Advice {
        if granted { UserDefaults.standard.removeObject(forKey: ScreenAccess.sentKey) }
        let entry = granted ? nil : await Task.detached(priority: .utility) { ScreenAccess.entry() }.value
        return ScreenAccess.advice(granted: granted, sentToSettings: UserDefaults.standard.bool(forKey: ScreenAccess.sentKey), entry: entry)
    }

    private static func av(_ s: AVAuthorizationStatus) -> Grant {
        s == .authorized ? .allowed : s == .notDetermined ? .notAsked : .denied
    }

    private static func un(_ s: UNAuthorizationStatus) -> Grant {
        s == .authorized || s == .provisional ? .allowed : s == .notDetermined ? .notAsked : .denied
    }

    private static func ek(_ s: EKAuthorizationStatus) -> Grant {
        s == .fullAccess ? .allowed : s == .notDetermined ? .notAsked : .denied
    }

    private static func cn(_ s: CNAuthorizationStatus) -> Grant {
        s == .authorized ? .allowed : s == .notDetermined ? .notAsked : .denied
    }

    /// AEDeterminePermissionToAutomateTarget blocks, so it runs off the main thread. `launch` opens the
    /// app hidden first (macOS can only answer for a running app).
    nonisolated static func automationGrant(_ t: AutomationTarget, ask: Bool, launch: Bool = false) async -> Grant {
        if launch { await HiddenLaunch.open(t.bundleID) }
        let status: OSStatus = await Task.detached {
            let target = NSAppleEventDescriptor(bundleIdentifier: t.bundleID)
            guard let desc = target.aeDesc else { return OSStatus(procNotFound) }
            return AEDeterminePermissionToAutomateTarget(desc, typeWildCard, typeWildCard, ask)
        }.value
        switch status {
        case noErr: return .allowed
        case OSStatus(errAEEventNotPermitted): return .denied
        case OSStatus(procNotFound): return .notRunning
        default: return .notAsked   // errAEEventWouldRequireUserConsent
        }
    }

    /// Signed with a real certificate (Apple Development or the local "Maxie Local Signing")? Ad-hoc builds
    /// get a new code identity every build and macOS forgets their permissions.
    static func isStablySigned() -> Bool {
        var code: SecCode?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code else { return false }
        var staticCode: SecStaticCode?
        guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess, let staticCode else { return false }
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
              let dict = info as? [String: Any] else { return false }
        return (dict[kSecCodeInfoCertificates as String] as? [Any])?.isEmpty == false
    }
}

/// Location for About me: asks once, reads one fix, turns it into a city. Nothing leaves the Mac except
/// the city to the local hub.
@MainActor
final class LocationAsker: NSObject, CLLocationManagerDelegate {
    struct Place: Sendable { let name: String; let lat: Double; let lon: Double; let timezone: String }

    private let manager = CLLocationManager()
    var onPlace: (@MainActor (Place) -> Void)?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyKilometer
    }

    var grant: Grant {
        switch manager.authorizationStatus {
        case .authorizedAlways: .allowed
        case .notDetermined: .notAsked
        default: .denied
        }
    }

    func ask() {
        if manager.authorizationStatus == .notDetermined {
            manager.requestWhenInUseAuthorization()
        } else if grant == .allowed {
            manager.requestLocation()
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in if self.grant == .allowed { self.manager.requestLocation() } }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor in await self.name(loc) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Log.d("location: \(error.localizedDescription)")
    }

    private func name(_ loc: CLLocation) async {
        if let place = await Self.place(from: loc) { onPlace?(place) }
    }

    /// "Austin, TX" and its time zone, reverse-geocoded on the Mac (also used by LocationFollower).
    static func place(from loc: CLLocation) async -> Place? {
        guard let mark = try? await CLGeocoder().reverseGeocodeLocation(loc).first else { return nil }
        let city = [mark.locality, mark.administrativeArea].compactMap { $0 }.joined(separator: ", ")
        guard !city.isEmpty else { return nil }
        return Place(name: city, lat: loc.coordinate.latitude, lon: loc.coordinate.longitude,
                     timezone: mark.timeZone?.identifier ?? TimeZone.current.identifier)
    }
}

extension Notification.Name {
    /// A permission prompt or System Settings trip Maxie started is over: his Settings comes back.
    static let maxieComesBack = Notification.Name("maxie.comesBack")
}
