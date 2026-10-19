import XCTest
@testable import ClaudeNotch

/// Settings › Connections › Google and who can use Maxie, both managed in the hub's web Settings.
@MainActor
final class ConnectionsTests: XCTestCase {
    func testTheBrowserStepIsSaidPlainly() {
        XCTAssertEqual(GoogleCard.waitingLine("waiting for consent (main)"), "Finish signing in in your browser…")
        XCTAssertEqual(GoogleCard.waitingLine("not signed in: sign-in was cancelled"), "Not signed in: sign-in was cancelled.")
        XCTAssertNil(GoogleCard.waitingLine(""))
        XCTAssertNil(GoogleCard.waitingLine("signed in as alex@example.com"), "a finished sign-in needs no line")
    }

    func testHubErrorsReadAsWords() {
        XCTAssertEqual(GoogleCard.detail(#"{"detail":"No Google client on this Mac yet."}"#), "No Google client on this Mac yet.")
        XCTAssertEqual(GoogleCard.detail("plain"), "plain")
    }

    func testTheConnectedAccountComesWithItsPhoto() throws {
        let json = #"{"client":true,"login_status":"","accounts":{"main":{"connected":true,"email":"alex@example.com","name":"Alex Rivera","picture":"https://p"},"job":{"connected":false,"email":"alex.jobs@example.com","name":"","picture":""}},"refused":[]}"#
        let g = try JSONDecoder().decode(ConnectionsSection.Google.self, from: Data(json.utf8))
        XCTAssertEqual(g.accounts?["main"]?.picture, "https://p")
        XCTAssertEqual(g.accounts?["main"]?.email, "alex@example.com")
        XCTAssertEqual(g.accounts?["job"]?.connected, false)
    }

    func testTheAccountsKeepASteadyOrder() throws {
        let json = #"{"client":true,"login_status":"","accounts":{"zeta":{"connected":true,"email":"z@example.com","name":"","picture":""},"job":{"connected":false,"email":"","name":"","picture":""},"main":{"connected":true,"email":"alex@example.com","name":"Alex Rivera","picture":""},"alpha":{"connected":false,"email":"","name":"","picture":""}}}"#
        let g = try JSONDecoder().decode(ConnectionsSection.Google.self, from: Data(json.utf8))
        XCTAssertEqual(GoogleCard.orderedLabels(g.accounts), ["main", "job", "alpha", "zeta"])
        XCTAssertEqual(GoogleCard.orderedLabels(nil), [])
    }

    /// Calendar answered 403; Connections says why and offers the one fix that works.
    func testARefusedCalendarSaysWhyAndTheFix() throws {
        let json = #"{"client":true,"login_status":"","refused":[{"service":"Calendar","why":"api_off","line":"The Google Calendar API is turned off…","fix_url":"https://console.developers.google.com/apis/api/calendar-json.googleapis.com/overview?project=1"},{"service":"Calendar","why":"scope","line":"Sign in with Google again…","fix_url":""}]}"#
        let g = try JSONDecoder().decode(ConnectionsSection.Google.self, from: Data(json.utf8))
        let refused = try XCTUnwrap(g.refused)
        XCTAssertEqual(refused[0].fix, .enable(URL(string: refused[0].fix_url)!))
        XCTAssertEqual(refused[1].fix, .signInAgain)
        let other = ConnectionsSection.Refusal(service: "Calendar", why: "other", line: "", fix_url: "")
        XCTAssertEqual(other.fix, .signInAgain, "a fresh sign-in is the usual cure")
        let old = try JSONDecoder().decode(ConnectionsSection.Google.self, from: Data(#"{"client":true,"login_status":""}"#.utf8))
        XCTAssertNil(old.refused, "a hub without the field still decodes")
        XCTAssertNil(old.accounts)
    }

    func testAnOlderHubWithMainAndJobFlagsStillDecodes() throws {
        let json = #"{"client":false,"main":true,"job":false,"login_status":"","accounts":{}}"#
        let g = try JSONDecoder().decode(ConnectionsSection.Google.self, from: Data(json.utf8))
        XCTAssertEqual(GoogleCard.orderedLabels(g.accounts), [])
    }

    func testManageInBrowserOpensTheRightPartOfWebSettings() {
        let base = URL(string: "http://127.0.0.1:8780")!
        XCTAssertEqual(HubClient.webURL(base, page: "/settings.html", fragment: "google").absoluteString,
                       "http://127.0.0.1:8780/settings.html#google")
        XCTAssertEqual(HubClient.webURL(base, page: "/settings.html", fragment: "access").absoluteString,
                       "http://127.0.0.1:8780/settings.html#access")
        XCTAssertEqual(HubClient.webURL(base, page: "/settings.html").absoluteString, "http://127.0.0.1:8780/settings.html")
    }
}
