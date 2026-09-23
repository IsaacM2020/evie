import Foundation

enum PTTInput: Equatable {
    case fnDown(at: TimeInterval)
    case fnUp(at: TimeInterval)
    case otherKey
}

enum PTTAction: Equatable {
    case none, startRecording, stopAndSend, cancel
}

// Hold Fn/Globe to talk. Pure logic, no AppKit, so the selftest can drive it.
// A quick tap or Fn+another key (Fn+arrow = Page Up etc.) is not speech, so it's cancelled.
struct PushToTalk {
    static let minHold: TimeInterval = 0.25
    private(set) var downAt: TimeInterval?

    mutating func handle(_ input: PTTInput) -> PTTAction {
        switch input {
        case .fnDown(let t):
            guard downAt == nil else { return .none }
            downAt = t
            return .startRecording
        case .otherKey:
            guard downAt != nil else { return .none }
            downAt = nil
            return .cancel
        case .fnUp(let t):
            guard let d = downAt else { return .none }
            downAt = nil
            return t - d >= Self.minHold ? .stopAndSend : .cancel
        }
    }
}
