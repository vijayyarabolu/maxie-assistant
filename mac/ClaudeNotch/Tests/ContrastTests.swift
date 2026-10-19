import AppKit
import SwiftUI
import XCTest
@testable import ClaudeNotch

/// Measured from pixels: every Settings section rendered off-screen in light and dark, over a black
/// and a white desktop (the field is 90% opaque glass). Cards: fill ≥ 1.25:1 and a hairline ≥ 3:1 against the
/// field around them. Controls: an edge or fill ≥ 3:1. Text: body ≥ 7:1, quiet and faint ≥ 4.5:1.
@MainActor
final class ContrastTests: XCTestCase {
    struct RGB { var r, g, b: Double }

    static func linear(_ c: Double) -> Double { c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
    static func lum(_ c: RGB) -> Double { 0.2126 * linear(c.r) + 0.7152 * linear(c.g) + 0.0722 * linear(c.b) }
    static func ratio(_ a: RGB, _ b: RGB) -> Double {
        let (x, y) = (lum(a), lum(b))
        return (max(x, y) + 0.05) / (min(x, y) + 0.05)
    }

    /// The host drawn to RGBA, flattened over a backdrop grey level (0 black, 1 white).
    @MainActor struct Shot {
        let w, h: Int
        let scale: CGFloat
        let px: [RGB]

        init(_ view: NSView, over backdrop: Double) {
            let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds)!
            view.cacheDisplay(in: view.bounds, to: rep)
            let cg = rep.cgImage!
            w = cg.width; h = cg.height
            scale = CGFloat(w) / view.bounds.width
            var raw = [UInt8](repeating: 0, count: w * h * 4)
            let ctx = CGContext(data: &raw, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
            ctx.setFillColor(CGColor(gray: backdrop, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
            ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
            px = stride(from: 0, to: raw.count, by: 4).map { RGB(r: Double(raw[$0]) / 255, g: Double(raw[$0 + 1]) / 255, b: Double(raw[$0 + 2]) / 255) }
        }

        /// Pixels inside `outer` but not inside `inner` (points, top-left origin).
        func ring(_ outer: CGRect, minus inner: CGRect?) -> [RGB] {
            var out: [RGB] = []
            let o = outer.applying(.init(scaleX: scale, y: scale)), i = inner?.applying(.init(scaleX: scale, y: scale))
            for y in max(0, Int(o.minY))..<min(h, Int(o.maxY)) {
                for x in max(0, Int(o.minX))..<min(w, Int(o.maxX)) where !(i?.contains(CGPoint(x: x, y: y)) ?? false) {
                    out.append(px[y * w + x])
                }
            }
            return out
        }
    }

    static func median(_ c: [RGB]) -> RGB {
        func m(_ k: KeyPath<RGB, Double>) -> Double { let s = c.map { $0[keyPath: k] }.sorted(); return s.isEmpty ? 0 : s[s.count / 2] }
        return RGB(r: m(\.r), g: m(\.g), b: m(\.b))
    }

    static func percentile(_ v: [Double], _ p: Double) -> Double {
        let s = v.sorted(); return s.isEmpty ? 0 : s[min(s.count - 1, Int(Double(s.count) * p))]
    }

    func resolved(_ c: Color, _ a: NSAppearance) -> (RGB, Double) {
        var out = (RGB(r: 0, g: 0, b: 0), 1.0)
        a.performAsCurrentDrawingAppearance {
            let n = NSColor(c).usingColorSpace(.sRGB)!
            out = (RGB(r: n.redComponent, g: n.greenComponent, b: n.blueComponent), n.alphaComponent)
        }
        return out
    }

    func testEverySettingsSectionHasVisibleStructureAndReadableText() {
        var failures: [String] = []
        for dark in [false, true] {
            let appearance = NSAppearance(named: dark ? .darkAqua : .aqua)!
            for section in SettingsSection.allCases {
                let hub = HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil)
                hub.maxie = try? JSONDecoder().decode(MaxieInfo.self, from: Data(ClickTests.maxieJSON.utf8))
                let h = ClickHarness(SettingsView(hub: hub, notch: NotchController(hub: hub), initial: section)
                    .environment(\.colorScheme, dark ? .dark : .light), size: SettingsWindow.size)
                h.window.appearance = appearance
                h.settle(0.6)
                let probes = ClickTargets.frames.filter { $0.key.hasPrefix("probe.") }
                    .filter { $0.value.minY > 40 && $0.value.maxY < SettingsWindow.size.height - 4 && $0.value.width > 4 }
                for backdrop in [0.0, 1.0] {
                    let shot = Shot(h.host, over: backdrop)
                    var fields: [RGB] = []
                    for (id, f) in probes {
                        let kind = id.split(separator: ".")[1]
                        let field = Self.median(shot.ring(f.insetBy(dx: -7, dy: -7), minus: f.insetBy(dx: -3, dy: -3)))
                        fields.append(field)
                        let band = shot.ring(f, minus: f.insetBy(dx: 2, dy: 2)).map { Self.ratio($0, field) }
                        let edge = Self.percentile(band, 0.9)
                        let fill = Self.ratio(Self.median(shot.ring(f.insetBy(dx: 4, dy: 4), minus: nil)), field)
                        let tag = "\(dark ? "dark" : "light")/\(backdrop == 0 ? "black" : "white")/\(section.rawValue)/\(id.dropFirst(6))@\(Int(f.minX)),\(Int(f.minY))"
                        if kind == "card" {
                            if fill < 1.25 { failures.append("\(tag): card fill \(String(format: "%.2f", fill)) < 1.25") }
                            if edge < 3 { failures.append("\(tag): card hairline \(String(format: "%.2f", edge)) < 3") }
                        } else if max(edge, fill) < 3 {
                            failures.append("\(tag): control edge \(String(format: "%.2f", edge)) / fill \(String(format: "%.2f", fill)) < 3")
                        }
                    }
                    let field = fields.max { Self.lum($0) < Self.lum($1) } ?? RGB(r: 0, g: 0, b: 0)  // the worst case for dark words
                    let lightest = dark ? field : (fields.min { Self.lum($0) < Self.lum($1) } ?? field)
                    for (name, token, need) in [("words", Light.words, 7.0), ("quiet", Light.quiet, 4.5), ("faint", Light.faint, 4.5)] {
                        let (c, a) = resolved(token, appearance)
                        let on = RGB(r: c.r * a + lightest.r * (1 - a), g: c.g * a + lightest.g * (1 - a), b: c.b * a + lightest.b * (1 - a))
                        let r = Self.ratio(on, lightest)
                        if r < need { failures.append("\(dark ? "dark" : "light")/\(backdrop == 0 ? "black" : "white")/\(section.rawValue): \(name) text \(String(format: "%.2f", r)) < \(need)") }
                    }
                }
                h.close()
            }
        }
        XCTAssertEqual(failures.count, 0, "\n" + Array(Set(failures)).sorted().prefix(60).joined(separator: "\n"))
    }
}
