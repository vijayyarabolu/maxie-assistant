import CoreGraphics
import Foundation

/// Maxie glitched when the pointer went near him at the top. Every tracking-area enter and exit
/// acted at once: a pass by opened the chat, a tucked Maxie slid out and in again as the pointer sat on his edge,
/// and his own slide moved the edge under the pointer. Now the pointer has to be on him for a moment to count as
/// in, and well off him (his frame grown by a dead zone) for a moment to count as out.
struct HoverGate {
    /// How far off him the pointer must go before it counts as out, in points.
    static let deadZone: CGFloat = 10
    /// How long a change must hold before it counts.
    static let settle: TimeInterval = 0.15

    private(set) var inside = false
    private var since: TimeInterval?  // when the pointer first looked like the other answer

    /// The pointer at `p` (screen points) at `now` (seconds), his window at `frame`. The new answer when it changes.
    mutating func update(_ p: CGPoint, frame: CGRect, now: TimeInterval) -> Bool? {
        let looks = inside ? frame.insetBy(dx: -Self.deadZone, dy: -Self.deadZone).contains(p) : frame.contains(p)
        guard looks != inside else {
            since = nil
            return nil
        }
        guard let start = since else {
            since = now
            return nil
        }
        guard now - start >= Self.settle else { return nil }
        inside = looks
        since = nil
        return looks
    }

    /// Still worth watching the pointer: it is near him or the answer is changing.
    func watching(_ p: CGPoint, frame: CGRect) -> Bool {
        inside || since != nil || frame.insetBy(dx: -Self.deadZone, dy: -Self.deadZone).contains(p)
    }
}
