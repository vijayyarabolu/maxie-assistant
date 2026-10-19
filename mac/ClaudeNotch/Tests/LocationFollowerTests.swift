import CoreLocation
import XCTest
@testable import ClaudeNotch

/// About me follows the user, low power, only when they want it and macOS allows it.
@MainActor
final class LocationFollowerTests: XCTestCase {
    func testTheClockHasItsEverydayName() {
        XCTAssertEqual(LocationFollower.zoneName(TimeZone(identifier: "America/Chicago")!), "Central")
        XCTAssertEqual(LocationFollower.zoneName(TimeZone(identifier: "America/Denver")!), "Mountain")
        XCTAssertEqual(LocationFollower.zoneName(TimeZone(identifier: "America/New_York")!), "Eastern")
        XCTAssertEqual(LocationFollower.zoneName(TimeZone(identifier: "America/Los_Angeles")!), "Pacific")
    }

    func testItWatchesOnlyWhenAllowedAndFollowing() {
        XCTAssertTrue(LocationFollower.shouldWatch(grant: .allowed, following: true))
        XCTAssertFalse(LocationFollower.shouldWatch(grant: .allowed, following: false), "a fixed city")
        XCTAssertFalse(LocationFollower.shouldWatch(grant: .notAsked, following: true))
        XCTAssertFalse(LocationFollower.shouldWatch(grant: .denied, following: true))
    }

    func testAMoveIsSentWithTheClocksName() async {
        var sent: [[String: Any]] = []
        let f = LocationFollower(send: { sent.append($0) })
        let place = LocationAsker.Place(name: "Austin, Texas", lat: 30.27, lon: -97.74, timezone: "America/Chicago")
        await f.report(place)
        XCTAssertEqual(sent.count, 1)
        XCTAssertEqual(sent[0]["name"] as? String, "Austin, Texas")
        XCTAssertEqual(sent[0]["timezone"] as? String, "America/Chicago")
        XCTAssertEqual(sent[0]["zone_name"] as? String, "Central")
        XCTAssertEqual(sent[0]["first"] as? Bool, false)
    }

    func testTheSameCityTwiceIsSentOnce() async {
        var sent: [[String: Any]] = []
        let f = LocationFollower(send: { sent.append($0) })
        let place = LocationAsker.Place(name: "Austin, Texas", lat: 30.27, lon: -97.74, timezone: "America/Chicago")
        await f.report(place)
        await f.report(LocationAsker.Place(name: "Austin, Texas", lat: 30.28, lon: -97.75, timezone: "America/Chicago"))
        XCTAssertEqual(sent.count, 1, "a significant change inside the same city isn't news")
    }

    func testAboutMeSaysWhereTheCityComesFrom() {
        XCTAssertTrue(AboutMeSection.followsLocation(following: true, grant: .allowed))
        XCTAssertFalse(AboutMeSection.followsLocation(following: true, grant: .notAsked), "no permission: the user types it")
        XCTAssertFalse(AboutMeSection.followsLocation(following: false, grant: .allowed), "a fixed city")
        XCTAssertEqual(AboutMeSection.followLine(following: true, grant: .notAsked), "Turn on Location so your city and clock follow you.")
        XCTAssertEqual(AboutMeSection.followLine(following: false, grant: .allowed), "Off: the city you set below stays.")
    }
}
