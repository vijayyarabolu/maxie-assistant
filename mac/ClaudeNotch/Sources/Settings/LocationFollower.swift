import CoreLocation
import Foundation

extension Notification.Name {
    /// About me › Follow my location was switched (object: Bool).
    static let followLocationChanged = Notification.Name("maxie.followLocationChanged")
}

/// About me follows the user. Significant-change monitoring only (no GPS, low power), the city and time
/// zone reverse-geocoded on the Mac (CLGeocoder, CLPlacemark.timeZone), then sent to the local hub, which
/// updates About me and says a real move once in the panel. Off while the user keeps a fixed city or Location isn't
/// allowed. Nothing but the city and its zone leaves the Mac, and only to the hub on this Mac.
@MainActor
final class LocationFollower: NSObject, CLLocationManagerDelegate {
    static let shared = LocationFollower()

    private let manager = CLLocationManager()
    private var send: @MainActor ([String: Any]) async -> Void
    private var following = false
    private var watching = false
    private var lastCity: String?

    init(send: @escaping @MainActor ([String: Any]) async -> Void = { _ in }) {
        self.send = send
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyKilometer
        NotificationCenter.default.addObserver(forName: .followLocationChanged, object: nil, queue: .main) { [weak self] n in
            let on = n.object as? Bool ?? true
            MainActor.assumeIsolated { self?.setFollowing(on) }
        }
    }

    /// At launch: send moves to this hub, and follow if About me says so (on by default).
    func attach(_ hub: HubClient) async {
        send = { body in _ = await hub.postJSON("/api/place/here", dict: body) }
        struct Follow: Decodable { let follow_location: Bool? }
        let s: Follow? = await hub.fetch("/api/settings")
        setFollowing(s?.follow_location ?? true)
    }

    var grant: Grant { Self.grant(manager.authorizationStatus) }

    static func grant(_ status: CLAuthorizationStatus) -> Grant {
        switch status {
        case .authorizedAlways: .allowed
        case .notDetermined: .notAsked
        default: .denied
        }
    }

    static func shouldWatch(grant: Grant, following: Bool) -> Bool { grant == .allowed && following }

    /// "Central" for America/Chicago: the zone's everyday English name without "Standard Time" / "Time".
    static func zoneName(_ zone: TimeZone) -> String {
        let name = zone.localizedName(for: .generic, locale: Locale(identifier: "en_US")) ?? zone.identifier
        for tail in [" Standard Time", " Time"] where name.hasSuffix(tail) {
            return String(name.dropLast(tail.count))
        }
        return name
    }

    /// About me's "Turn on": the real macOS prompt while it can still ask.
    func askPermission() {
        if manager.authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
    }

    func setFollowing(_ on: Bool) {
        following = on
        update()
    }

    private func update() {
        let should = Self.shouldWatch(grant: grant, following: following)
        guard should != watching else { return }
        watching = should
        if should { manager.startMonitoringSignificantLocationChanges() } else { manager.stopMonitoringSignificantLocationChanges() }
    }

    /// Sends a place unless it's the city the user is already in.
    func report(_ place: LocationAsker.Place, first: Bool = false) async {
        let city = place.name.split(separator: ",").first.map { $0.trimmingCharacters(in: .whitespaces) } ?? place.name
        guard city != lastCity else { return }
        lastCity = city
        let zone = TimeZone(identifier: place.timezone).map(Self.zoneName) ?? ""
        await send(["name": place.name, "lat": place.lat, "lon": place.lon, "timezone": place.timezone,
                    "zone_name": zone, "first": first])
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in self.update() }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor in
            if let place = await LocationAsker.place(from: loc) { await self.report(place) }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Log.d("location follow: \(error.localizedDescription)")
    }
}
