import XCTest
@testable import ClaudeNotch

/// Watch me once: the replay finds controls by what they are called, and steps travel to the hub in its shape.
final class FlowTests: XCTestCase {
    struct Node: FlowNode {
        var role = "AXGroup", title = "", label = "", identifier = ""
        var children: [Node] = []
    }

    private func click(role: String = "AXButton", title: String = "", label: String = "", id: String = "") -> FlowStep {
        var s = FlowStep(kind: .click)
        s.role = role; s.title = title; s.label = label; s.id = id
        return s
    }

    private let window = Node(children: [
        Node(role: "AXToolbar", children: [Node(role: "AXButton", title: "New Report"), Node(role: "AXButton", label: "Share")]),
        Node(children: [Node(role: "AXTextField", label: "Amount"), Node(role: "AXButton", title: "Save", identifier: "save-btn"),
                        Node(role: "AXStaticText", title: "Save")]),
        Node(children: [Node(role: "AXButton", title: "Save as draft")]),
    ])

    func testFindsByIdFirst() {
        XCTAssertEqual(FlowMatcher.find(in: window, step: click(title: "Whatever", id: "save-btn"))?.identifier, "save-btn")
    }

    func testFindsBySameKindAndNameIgnoringCase() {
        XCTAssertEqual(FlowMatcher.find(in: window, step: click(title: "new report"))?.title, "New Report")
        XCTAssertEqual(FlowMatcher.find(in: window, step: click(role: "AXTextField", label: "Amount"))?.role, "AXTextField")
    }

    func testExactBeatsContainsAndRoleMustMatch() {
        XCTAssertEqual(FlowMatcher.find(in: window, step: click(title: "Save"))?.identifier, "save-btn")
        XCTAssertNil(FlowMatcher.find(in: window, step: click(role: "AXCheckBox", title: "Save")))
    }

    func testFallsBackToANameThatContainsIt() {
        XCTAssertEqual(FlowMatcher.find(in: window, step: click(title: "as draft"))?.title, "Save as draft")
    }

    func testGivesUpRatherThanGuess() {
        XCTAssertNil(FlowMatcher.find(in: window, step: click(title: "Delete everything")))
        XCTAssertNil(FlowMatcher.find(in: window, step: click(title: "Sa")), "two letters are too little to go on")
    }

    func testStepsUseTheHubsShape() throws {
        let key = FlowStep.press("s", mods: ["cmd"])
        XCTAssertEqual(key.json as NSDictionary, ["do": "key", "key": "s", "mods": ["cmd"]] as NSDictionary)
        XCTAssertEqual(FlowStep.typing("Team lunch").json as NSDictionary, ["do": "type", "text": "Team lunch"] as NSDictionary)
        let raw = #"{"do": "click", "app": "com.apple.Safari", "role": "AXButton", "title": "New Report"}"#
        let back = try JSONDecoder().decode(FlowStep.self, from: Data(raw.utf8))
        XCTAssertEqual(back.kind, .click)
        XCTAssertEqual(back.title, "New Report")
        XCTAssertEqual(back.label, "")
    }

    func testKeyNamesRoundTrip() {
        for (name, code) in FlowKeys.codes { XCTAssertEqual(FlowKeys.names[code], name) }
    }

    func testNamingLineSaysWhatWasSeen() {
        let steps: [FlowStep] = [.openApp(bundle: "com.apple.Safari", name: "Safari"), click(title: "New"),
                                 .typing("x"), .openApp(bundle: "com.apple.mail", name: "Mail"), .press("return")]
        XCTAssertEqual(NameFlowView.summary(steps), "I saw 3 steps in Safari and Mail. What should I call it?")
    }
}
