import SwiftUI

/// Where each tappable control sits, by a stable id ("settings.row.General", "word.Medium", "swatch.ocean"), in
/// window coordinates. Only recorded while the click tests run, so they can click the exact centre
/// of a real control; in the app it costs nothing.
enum ClickTargets {
    // Main thread only (SwiftUI builds and lays out views there); not actor-isolated so text field styles,
    // which SwiftUI calls nonisolated, can mark themselves too.
    nonisolated(unsafe) static var isRecording = false
    nonisolated(unsafe) static private(set) var frames: [String: CGRect] = [:]
    /// What a view is showing right now ("settings.section" -> "General"), so a test can see a click's effect.
    nonisolated(unsafe) static private(set) var values: [String: String] = [:]

    static func record(_ id: String, _ frame: CGRect) { if isRecording { frames[id] = frame } }
    /// A control that left the screen (a "Turn on" that became "Allowed") is no longer a target.
    static func forget(_ id: String) { if isRecording { frames[id] = nil } }
    static func note(_ id: String, _ value: String) { if isRecording { values[id] = value } }
    static func reset() { frames = [:]; values = [:] }
}

extension View {
    /// Reports this view's frame (window coordinates, top-left origin) under `id` while click tests record.
    func clickTarget(_ id: String) -> some View {
        background {
            if ClickTargets.isRecording {
                GeometryReader { g in
                    Color.clear
                        .onAppear { ClickTargets.record(id, g.frame(in: .global)) }
                        .onChange(of: g.frame(in: .global)) { _, f in ClickTargets.record(id, f) }
                        .onDisappear { ClickTargets.forget(id) }
                }
            }
        }
    }

    /// Reports what this view shows under `id` while click tests record.
    func clickValue(_ id: String, _ value: String) -> some View {
        onChange(of: value, initial: true) { _, v in ClickTargets.note(id, v) }
    }
}

/// Marks a card or control for the contrast test: "probe.card.<id>", "probe.control.<id>".
private struct Probe: ViewModifier {
    let kind: String
    @State private var id = UUID().uuidString
    func body(content: Content) -> some View { content.clickTarget("probe.\(kind).\(id)") }
}

extension View {
    func probe(_ kind: String) -> some View { modifier(Probe(kind: kind)) }
}
