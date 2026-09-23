import Foundation

enum PTTInput: Equatable {
    case keyDown(at: TimeInterval)
    case keyUp(at: TimeInterval)
    case otherKey
}

enum PTTAction: Equatable {
    case none, startRecording, stopAndSend, cancel
}

// Hold the talk chord (left ⌃⌥) to talk. Pure logic, no AppKit, so the selftest can drive it.
// A quick tap, or the chord plus another key (a ⌃⌥ shortcut in some app), is not speech: cancelled.
struct PushToTalk {
    static let minHold: TimeInterval = 0.25
    private(set) var downAt: TimeInterval?

    mutating func handle(_ input: PTTInput) -> PTTAction {
        switch input {
        case .keyDown(let t):
            guard downAt == nil else { return .none }
            downAt = t
            return .startRecording
        case .otherKey:
            guard downAt != nil else { return .none }
            downAt = nil
            return .cancel
        case .keyUp(let t):
            guard let d = downAt else { return .none }
            downAt = nil
            return t - d >= Self.minHold ? .stopAndSend : .cancel
        }
    }
}

// Reads the modifier keys into Evie's two chords. Left keys only (Right Option stays Ripple's):
//   left ⌃ + left ⌥          hold to talk
//   left ⌃ + left ⌥ + ⌘      flip the Live open mic on/off (once per press)
// Pressing ⌃⌥⌘ passes through ⌃⌥ for a few ms, so a talk that just started is cancelled.
struct Chord {
    // NX_DEVICE* bits in NSEvent.modifierFlags.rawValue: they tell left and right keys apart.
    static let leftControl: UInt = 0x1, leftOption: UInt = 0x20, leftCommand: UInt = 0x8, rightCommand: UInt = 0x10

    enum Out: Equatable { case talkDown, talkUp, talkCancel, liveToggle }

    private(set) var talking = false
    private var armed = true  // false after a toggle until the chord is fully released

    mutating func update(raw: UInt) -> [Out] {
        let base = raw & Self.leftControl != 0 && raw & Self.leftOption != 0
        let command = raw & (Self.leftCommand | Self.rightCommand) != 0
        var out: [Out] = []
        if base && command {
            if talking { talking = false; out.append(.talkCancel) }
            if armed { armed = false; out.append(.liveToggle) }
        } else if base {
            if !talking && armed { talking = true; out.append(.talkDown) }
        } else {
            if talking { talking = false; out.append(.talkUp) }
            armed = true
        }
        return out
    }
}
