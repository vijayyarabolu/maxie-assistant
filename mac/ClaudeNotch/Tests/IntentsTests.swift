import XCTest
@testable import ClaudeNotch

/// Siri / Shortcuts / Spotlight reach Maxie.
final class IntentsTests: XCTestCase {
    func testTheAnswerIsTheDoneEventsText() {
        let body = """
            {"type":"route","route":"quick_question","model":null}
            {"type":"text","text":"It's "}
            {"type":"text","text":"9:54 PM."}
            {"type":"done","text":"It's 9:54 PM.","model":null,"cost":0.0}
            """
        XCTAssertEqual(IntentHub.reply(fromNDJSON: body), "It's 9:54 PM.")
    }

    func testFallsBackToStreamedWordsAndIgnoresJunk() {
        XCTAssertEqual(IntentHub.reply(fromNDJSON: "not json\n{\"type\":\"text\",\"text\":\" Hi \"}\n{\"type\":\"done\",\"text\":\"\"}"), "Hi")
        XCTAssertNil(IntentHub.reply(fromNDJSON: ""))
    }

    func testNoTokenSaysWhatToDo() async {
        do {
            _ = try await IntentHub.ask("hi", token: nil)
            XCTFail("should throw")
        } catch IntentHub.Failure.noToken {
        } catch {
            XCTFail("\(error)")
        }
    }

    func testFourShortcutsAllNamingMaxie() {
        XCTAssertEqual(MaxieShortcuts.appShortcuts.count, 4)
    }

    func testMemoryFactsDecodeFromTheHub() {
        let json = #"{"markdown":"","topics":["people"],"facts":[{"id":3,"topic":"people","text":"Riley lives in Springfield","source":"chat","created":1791043200.0}]}"#
        XCTAssertEqual(MemoryIndex.decode(Data(json.utf8)), [MemoryFactEntity(id: 3, text: "Riley lives in Springfield", topic: "people")])
        XCTAssertEqual(MemoryIndex.decode(Data("oops".utf8)), [])
    }
}
