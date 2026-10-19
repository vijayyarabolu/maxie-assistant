"""the line down the left of the chat looked ugly. It's gone; each message keeps its own dot, so the
chat still reads as a timeline. SwiftUI views can't be looked up at run time, so this reads the Chat tab's source."""

from tests.test_mac_text import body


def chat() -> str:
    return body("Panel/ConversationView.swift", "struct ConversationView", "struct TurnRow")


def test_the_chat_has_no_line_down_its_side():
    assert "ThreadLine" not in chat()


def test_every_message_keeps_its_dot():
    row = body("Panel/ConversationView.swift", "struct TurnRow", "private var copyWord")
    assert "Knot(kind: fromUser ? .user : .claude" in row
