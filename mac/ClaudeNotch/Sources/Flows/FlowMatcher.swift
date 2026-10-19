import Foundation

/// A control in another app's Accessibility tree (AXNode on the Mac, plain structs in tests).
protocol FlowNode {
    var role: String { get }
    var title: String { get }
    var label: String { get }
    var identifier: String { get }
    var children: [Self] { get }
}

/// Finds the control a click step meant, the way a person would: the same id, else the same kind of control with
/// the same name, else one whose name contains it. Breadth first, so the nearest match wins a tie.
enum FlowMatcher {
    static let maxNodes = 5000
    static let enough = 25

    static func score(_ n: some FlowNode, for s: FlowStep) -> Int {
        if !s.id.isEmpty, n.identifier == s.id { return 100 }
        if !s.role.isEmpty, n.role != s.role { return 0 }
        let title = norm(n.title), label = norm(n.label), want = norm(s.title), wantLabel = norm(s.label)
        if !want.isEmpty, title == want { return 60 }
        if !wantLabel.isEmpty, label == wantLabel { return 50 }
        if !want.isEmpty, want.count >= 3, title.contains(want) || label.contains(want) { return 30 }
        if !wantLabel.isEmpty, wantLabel.count >= 3, label.contains(wantLabel) || title.contains(wantLabel) { return 25 }
        return 0
    }

    static func find<N: FlowNode>(in root: N, step: FlowStep) -> N? {
        var queue = [root], best: (Int, N)? = nil, seen = 0
        while !queue.isEmpty, seen < maxNodes {
            let n = queue.removeFirst()
            seen += 1
            let sc = score(n, for: step)
            if sc == 100 { return n }
            if sc > (best?.0 ?? 0) { best = (sc, n) }
            queue.append(contentsOf: n.children)
        }
        return (best?.0 ?? 0) >= enough ? best?.1 : nil
    }

    static func norm(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}
