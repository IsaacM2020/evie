import Foundation

struct CoreStatus: Decodable, Equatable {
    let ok: Bool
    let version: String
    let jevOk: Bool?
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

enum CoreJSON {
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}
