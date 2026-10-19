import XCTest
@testable import ClaudeNotch

/// The panel's frame memory, its keys, the sectioned brief, and the menu-bar glyph.
final class PanelTests: XCTestCase {
    let screen = CGRect(x: 0, y: 0, width: 1512, height: 944)
    let bot = CGRect(x: 854, y: 893, width: 30, height: 50)

    func testOpensNextToTheBotUntilMoved() {
        let r = PanelLayout.frame(saved: .init(), bot: bot, screen: screen, screens: [screen], atNotch: true)
        XCTAssertFalse(r.moved)
        XCTAssertEqual(r.frame.size, CGSize(width: 420, height: 620))
    }

    func testRemembersWhereHeMovedItAndHowTallHeMadeIt() {
        let saved = PanelLayout.Saved(origin: CGPoint(x: 100, y: 120), height: 500)
        let r = PanelLayout.frame(saved: saved, bot: bot, screen: screen, screens: [screen], atNotch: true)
        XCTAssertTrue(r.moved)
        XCTAssertEqual(r.frame, CGRect(x: 100, y: 120, width: 420, height: 500))
    }

    func testForgetsASpotOnADisplayThatIsGone() {
        let saved = PanelLayout.Saved(origin: CGPoint(x: 2400, y: 120), height: 500)
        let r = PanelLayout.frame(saved: saved, bot: bot, screen: screen, screens: [screen], atNotch: true)
        XCTAssertFalse(r.moved)
        XCTAssertEqual(r.frame.height, 500)
    }

    func testSaveAndLoadRoundTrip() throws {
        let d = try XCTUnwrap(UserDefaults(suiteName: "PanelTests-\(UUID())"))
        PanelLayout.save(CGRect(x: 10, y: 20, width: 420, height: 455), to: d)
        XCTAssertEqual(PanelLayout.load(d), .init(origin: CGPoint(x: 10, y: 20), height: 455))
    }

    /// Double-click Maxie and everything goes home; the chat forgets where it was dragged (it goes back
    /// beside him) but keeps the height the user gave it. The rest of the time the chat stays where the user puts it.
    func testGoingHomeForgetsWhereTheChatWasMovedButNotItsHeight() throws {
        let d = try XCTUnwrap(UserDefaults(suiteName: "PanelTests-\(UUID())"))
        PanelLayout.save(CGRect(x: 100, y: 120, width: 420, height: 500), to: d)
        PanelLayout.forgetSpot(in: d)
        XCTAssertEqual(PanelLayout.load(d), PanelLayout.Saved(origin: nil, height: 500))
        let r = PanelLayout.frame(saved: PanelLayout.load(d), bot: bot, screen: screen, screens: [screen], atNotch: true)
        XCTAssertFalse(r.moved, "back beside Maxie")
        XCTAssertEqual(r.frame.height, 500)
    }

    /// Maxie putting the chat beside him isn't the user's move: only frames Maxie didn't place himself are remembered.
    func testOnlyTheUsersOwnMovesOfTheChatAreRemembered() {
        let placed = CGRect(x: 300, y: 200, width: 420, height: 620)
        XCTAssertFalse(PanelLayout.isUserMove(placed, placed: placed))
        XCTAssertTrue(PanelLayout.isUserMove(placed.offsetBy(dx: 40, dy: 0), placed: placed))
        XCTAssertTrue(PanelLayout.isUserMove(placed, placed: nil))
    }

    func testBottomEdgeResizeKeepsTheTopAndStaysOnScreen() {
        let f = CGRect(x: 100, y: 300, width: 420, height: 600)
        XCTAssertEqual(PanelLayout.resized(f, to: 450, in: screen), CGRect(x: 100, y: 450, width: 420, height: 450))
        XCTAssertEqual(PanelLayout.resized(f, to: 50, in: screen).height, PanelLayout.minHeight)
        XCTAssertEqual(PanelLayout.resized(f, to: 5000, in: screen), CGRect(x: 100, y: 0, width: 420, height: 900))
    }

    func testPanelKeys() {
        XCTAssertEqual(PanelKey(keyCode: 53, characters: "\u{1b}", command: false), .close)
        XCTAssertEqual(PanelKey(keyCode: 43, characters: ",", command: true), .settings)
        XCTAssertEqual(PanelKey(keyCode: 12, characters: "q", command: true), .quit)
        XCTAssertNil(PanelKey(keyCode: 12, characters: "q", command: false))  // typing a q in the box
        XCTAssertNil(PanelKey(keyCode: 43, characters: ",", command: false))
    }

    func testBriefDecodesSectionsAndFixWords() throws {
        let json = """
        {"title": "Today so far", "date": "Thursday, October 15", "text": "x", "sections": [
          {"name": "Email", "lines": [], "missing": "Gmail not connected", "action": "connect_google"},
          {"name": "Jobs", "lines": [], "missing": "jobhunt not linked", "action": "link_jobhunt"},
          {"name": "Spend", "lines": ["API spend: $0.01 today"], "missing": "", "action": ""}]}
        """
        let b = try JSONDecoder().decode(BriefInfo.self, from: Data(json.utf8))
        XCTAssertEqual(b.title, "Today so far")
        XCTAssertEqual(b.sections.map(\.name), ["Email", "Jobs", "Spend"])
        XCTAssertEqual(b.sections.map { BriefInfo.actionWord($0.action) }, ["Connect", "Link", nil])
    }

    func testHintLineAndGlyph() {
        XCTAssertEqual(PanelHints.line, "Esc to close · ⌘, Settings · drag me anywhere · ⌥Space to open")
        let g = BotGlyph.image()
        XCTAssertTrue(g.isTemplate)
        XCTAssertEqual(g.size, BotGlyph.size)
    }
}
