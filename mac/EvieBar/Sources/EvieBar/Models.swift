import Foundation

struct JobDTO: Decodable, Equatable {
    let id: String
    let goal: String
    let status: String
    let started: Double
    let events: [String]
}

struct VoicePrintDTO: Decodable, Equatable {
    let clips: Int
    let seconds: Double
    let ready: Bool
}

// What /ears, /ears/mode and /voiceid/enroll return.
struct EarsDTO: Decodable, Equatable {
    let mode: String
    let enrolling: Bool
    let voiceprint: VoicePrintDTO
}

struct ShadowRow: Identifiable, Equatable {
    let id = UUID()
    let text: String
    let would: String
}

struct CoreStatus: Decodable, Equatable {
    let ok: Bool
    let version: String
    let jevOk: Bool?
    var sttReady: Bool? = nil
    var voiceReady: Bool? = nil
    var calendarFresh: Bool? = nil
    var job: JobDTO? = nil
    var earsMode: String? = nil  // nil: the core has no ear models, push-to-talk only
    var voiceprint: VoicePrintDTO? = nil
}

struct DecisionDTO: Decodable, Equatable {
    let forEvie: Double
    let route: String
    let routeConfidence: Double
    let complete: Double
    let hasEvent: Double
    let latencyMs: Double
}

struct OutcomeDTO: Decodable, Equatable {
    let action: String
    let reason: String
    let followup: Bool
    let decision: DecisionDTO?
}

// What /voice and /hear return: what Evie heard, her verdict, and what she said.
struct TurnDTO: Decodable, Equatable {
    let text: String
    let action: String
    let reason: String
    let route: String?
    let said: String?
}

// One event from the core's WebSocket. Every field but kind is optional; unknown keys are ignored.
struct CoreEvent: Decodable, Equatable {
    let kind: String
    var t: Double? = nil
    var text: String? = nil
    var state: String? = nil
    var action: String? = nil
    var reason: String? = nil
    var route: String? = nil
    var id: String? = nil
    var goal: String? = nil
    var line: String? = nil
    var status: String? = nil
    var summary: String? = nil
    var job: JobDTO? = nil
    // Phase 2: open mic
    var would: String? = nil
    var speaker: String? = nil
    var mode: String? = nil
    var enrolling: Bool? = nil
    var clips: Int? = nil
    var seconds: Double? = nil
    var ready: Bool? = nil
    var voiceprint: VoicePrintDTO? = nil
    // Phase 3: hands commands
    var op: String? = nil
    var args: [String: JSONValue]? = nil
    var expires: Double? = nil
    // Phase 3c: a job's own plan (step k of n) for the orb
    var done: Int? = nil
    var total: Int? = nil
    var step: String? = nil
    // Phase 4: "Which one?" rows, follow-ups, text mode, say-stop windows
    var options: [OptionRow]? = nil
    var asked: String? = nil
    var textOnly: Bool? = nil
    var about: String? = nil
    var ask: Bool? = nil
    var yes: Bool? = nil
    var spoken: Bool? = nil
    var why: String? = nil
    var setting: String? = nil
    var micPaused: String? = nil
}

struct QuietDTO: Decodable, Equatable {
    var mode: String = "voice"
    var why: String = ""
    var setting: String = "auto"
    var micPaused: String? = nil
}

struct SettingsDTO: Decodable {
    var output: QuietDTO? = nil
}

/// "SAX · 12m": the next thing on his calendar, when it's within half an hour.
enum NextUp {
    static func chip(_ events: [CalEventDTO], now: Date) -> String? {
        let soon = events.filter { !$0.allDay && $0.start > now && $0.start.timeIntervalSince(now) <= 1800 }
            .min { $0.start < $1.start }
        guard let e = soon else { return nil }
        let mins = Int(ceil(e.start.timeIntervalSince(now) / 60))
        let name = e.title.replacingOccurrences(of: #"(?i)\s+class$"#, with: "", options: .regularExpression)
        return "\(String(name.prefix(14))) · \(mins)m"
    }
}

enum CoreJSON {
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}
