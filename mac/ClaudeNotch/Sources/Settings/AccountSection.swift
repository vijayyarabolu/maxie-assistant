import SwiftUI

struct AccountSection: View {
    let hub: HubClient
    @State private var hasKey = false
    @State private var monthUSD = 0.0
    @State private var todayUSD = 0.0
    @State private var cap = 95.0
    @State private var newKey = ""
    @State private var note = ""

    struct Account: Decodable { let api_key: Bool; let month_usd: Double; let today_usd: Double; let cap_usd: Double }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                SettingRow(title: "Anthropic API key", subtitle: "Stored in the login Keychain under maxie. Never shown or logged.") {
                    StatusMark(ok: hasKey, yes: "In the Keychain", no: "Missing")
                }
                Color.clear.frame(height: 2)
                HStack(spacing: 8) {
                    SecureField("Paste a new key (sk-ant-…)", text: $newKey).textFieldStyle(.line)
                    Button("Replace") { replace() }.buttonStyle(.sunsetProminent).disabled(newKey.count < 20)
                }
                .padding(.top, 6)
            }
            SunsetCard {
                Text("Spend").font(.headline)
                HStack(alignment: .firstTextBaseline, spacing: 24) {
                    stat(String(format: "$%.2f", todayUSD), "today")
                    stat(String(format: "$%.2f", monthUSD), "this month")
                    stat(String(format: "$%.0f", cap), "monthly cap")
                }
                SpendBar(used: monthUSD, cap: cap).padding(.top, 4)
                Color.clear.frame(height: 2)
                SettingRow(title: "Monthly cap (USD)", subtitle: "Above it, Maxie only does direct Mac actions until the month resets.") {
                    HStack {
                        TextField("95", value: $cap, format: .number).textFieldStyle(.line).frame(width: 72)
                        Button("Save") { Task { await hub.putSettings(["monthly_budget_usd": cap]); await load(); note = "Cap saved." } }
                            .buttonStyle(.sunset)
                    }
                }
            }
            NoteLine(text: note)
        }
        .task { await load() }
    }

    private func stat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(value).font(.system(.title2, design: .rounded, weight: .bold)).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func load() async {
        if let a: Account = await hub.fetch("/api/account") {
            hasKey = a.api_key; monthUSD = a.month_usd; todayUSD = a.today_usd; cap = a.cap_usd
        }
    }

    private func replace() {
        Task {
            let (code, body) = await hub.postJSON("/api/account/key", dict: ["key": newKey])
            newKey = ""
            if code == 200 {
                note = "Key replaced. Restarting the hub…"
                MacHelpers.kickstart(MacHelpers.hubLabel)
                try? await Task.sleep(for: .seconds(3))
                await load()
                note = "Key replaced and the hub restarted."
            } else {
                note = "Not replaced: \(body)"
            }
        }
    }
}

/// How much of the month's credit is used: a thin bar with the sunset gradient.
struct SpendBar: View {
    let used: Double
    let cap: Double
    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(Sunset.accent.opacity(0.12))
                Capsule().fill(Sunset.sky).frame(width: max(6, g.size.width * min(1, cap > 0 ? used / cap : 0)))
            }
        }
        .frame(height: 8)
        .accessibilityLabel(String(format: "%.0f percent of the monthly cap used", cap > 0 ? used / cap * 100 : 0))
    }
}
