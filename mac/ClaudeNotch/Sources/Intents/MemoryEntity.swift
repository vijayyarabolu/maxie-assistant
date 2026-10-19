import AppIntents
import CoreSpotlight
import Foundation

/// Maxie's memory as App Entities, indexed in Spotlight, so Siri and Spotlight can find what Maxie knows
/// ("Riley lives in Springfield") and use it as personal context. Read from the hub; the index
/// lives on this Mac only.
struct MemoryFactEntity: IndexedEntity, Equatable {
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Maxie Memory"
    static let defaultQuery = MemoryFactQuery()

    let id: Int
    let text: String
    let topic: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(text)", subtitle: "Maxie remembers · \(topic)")
    }

    var attributeSet: CSSearchableItemAttributeSet {
        let a = CSSearchableItemAttributeSet()
        a.displayName = text
        a.contentDescription = "Maxie remembers (\(topic))"
        a.keywords = ["Maxie", topic]
        return a
    }
}

struct MemoryFactQuery: EntityStringQuery {
    func entities(for identifiers: [Int]) async throws -> [MemoryFactEntity] {
        try await MemoryIndex.facts().filter { identifiers.contains($0.id) }
    }

    func entities(matching string: String) async throws -> [MemoryFactEntity] {
        try await MemoryIndex.facts().filter { $0.text.localizedCaseInsensitiveContains(string) }
    }

    func suggestedEntities() async throws -> [MemoryFactEntity] {
        try await MemoryIndex.facts()
    }
}

enum MemoryIndex {
    /// The facts in the hub's memory (GET /api/memory).
    static func facts(token: String? = Keychain.read("HUB_TOKEN")) async throws -> [MemoryFactEntity] {
        guard let token, !token.isEmpty else { throw IntentHub.Failure.noToken }
        var r = URLRequest(url: IntentHub.base.appending(path: "api/memory"))
        r.timeoutInterval = 10
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let data: Data, response: URLResponse
        do { (data, response) = try await URLSession.shared.data(for: r) } catch { throw IntentHub.Failure.hubDown }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw IntentHub.Failure.badStatus(code) }
        return decode(data)
    }

    static func decode(_ data: Data) -> [MemoryFactEntity] {
        struct Fact: Decodable { let id: Int; let text: String; let topic: String }
        struct Body: Decodable { let facts: [Fact] }
        let body = try? JSONDecoder().decode(Body.self, from: data)
        return (body?.facts ?? []).map { MemoryFactEntity(id: $0.id, text: $0.text, topic: $0.topic) }
    }

    /// Re-index everything: forgotten facts leave Spotlight, new ones arrive. Quiet when the hub is down.
    static func refresh() async {
        do {
            let all = try await facts()
            let index = CSSearchableIndex.default()
            try await index.deleteAppEntities(ofType: MemoryFactEntity.self)
            try await index.indexAppEntities(all)
            Log.d("spotlight: indexed \(all.count) memories")
        } catch {
            Log.d("spotlight: memory index skipped (\(error))")
        }
    }
}
