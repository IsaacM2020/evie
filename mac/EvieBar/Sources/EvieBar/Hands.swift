import AppKit
import EventKit
import Foundation

// A loosely typed JSON value, for `do` command args (strings, numbers, bools).
enum JSONValue: Decodable, Equatable {
    case string(String), number(Double), bool(Bool), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else { self = .string(try c.decode(String.self)) }
    }

    var string: String? { if case .string(let s) = self { return s } else { return nil } }
    var bool: Bool? { if case .bool(let b) = self { return b } else { return nil } }
}

// Every command runs at most once, and never after it expired (an app that reconnects, or a
// core that restarted, must not replay "play" or "add event"). Pure, for the selftest.
struct HandsGate {
    private var seen: [String] = []

    mutating func admit(id: String, expires: Double, now: Double) -> Bool {
        guard now <= expires, !seen.contains(id) else { return false }
        seen.append(id)
        if seen.count > 200 { seen.removeFirst(100) }
        return true
    }

    static func isSpotifyURI(_ s: String) -> Bool {
        s.range(of: #"^spotify:(track|album|playlist|artist):[A-Za-z0-9]{10,40}$"#, options: .regularExpression) != nil
    }
}

struct HandsOutcome {
    let ok: Bool
    let detail: String
    var data: [String: String] = [:]
}

// The app's hands. Spotify goes through /usr/bin/osascript: macOS credits the Automation
// permission to Evie.app (the responsible app), so "Evie wants to control Spotify" is asked
// once and sticks. Calendar writes use EventKit, which the app already has full access to.
@MainActor
final class Hands {
    private var gate = HandsGate()
    private let store = EKEventStore()

    func run(_ ev: CoreEvent) async -> HandsOutcome? {
        guard let id = ev.id, let op = ev.op,
              gate.admit(id: id, expires: ev.expires ?? 0, now: Date().timeIntervalSince1970) else { return nil }
        let a = ev.args ?? [:]
        switch op {
        case "spotify_play":
            guard let uri = a["uri"]?.string, HandsGate.isSpotifyURI(uri) else {
                return HandsOutcome(ok: false, detail: "bad Spotify link")
            }
            return await spotify("play track \"\(uri)\"", launch: true)
        case "spotify_pause": return await spotify("pause", launch: false)
        case "spotify_resume": return await spotify("play", launch: true)
        case "spotify_next": return await spotify("next track", launch: false)
        case "spotify_previous": return await spotify("previous track", launch: false)
        case "spotify_state": return await spotifyState()
        case "calendar_add": return calendarAdd(a)
        case "calendar_delete": return calendarDelete(a["id"]?.string ?? "")
        default: return HandsOutcome(ok: false, detail: "I don't know how to \(op) yet")
        }
    }

    // MARK: Spotify

    private var spotifyRunning: Bool {
        !NSRunningApplication.runningApplications(withBundleIdentifier: "com.spotify.client").isEmpty
    }

    private func spotify(_ command: String, launch: Bool) async -> HandsOutcome {
        if !launch && !spotifyRunning { return HandsOutcome(ok: false, detail: "Spotify isn't open") }
        let r = await Self.osascript("tell application \"Spotify\" to \(command)")
        if !r.ok { return HandsOutcome(ok: false, detail: Self.friendly(r.out)) }
        return await spotifyState()
    }

    private func spotifyState() async -> HandsOutcome {
        guard spotifyRunning else { return HandsOutcome(ok: false, detail: "Spotify isn't open") }
        let script = """
        tell application "Spotify"
          set s to (player state as string)
          if s is "stopped" then return s & "|||"
          return s & "|" & (id of current track) & "|" & (name of current track) & "|" & (artist of current track)
        end tell
        """
        let r = await Self.osascript(script)
        guard r.ok else { return HandsOutcome(ok: false, detail: Self.friendly(r.out)) }
        let p = r.out.trimmingCharacters(in: .whitespacesAndNewlines).components(separatedBy: "|")
        guard p.count == 4 else { return HandsOutcome(ok: false, detail: "Spotify said something odd") }
        return HandsOutcome(ok: true, detail: p[0], data: ["state": p[0], "uri": p[1], "name": p[2], "artist": p[3]])
    }

    nonisolated static func osascript(_ script: String) async -> (ok: Bool, out: String) {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
                p.arguments = ["-e", script]
                let out = Pipe(), err = Pipe()
                p.standardOutput = out
                p.standardError = err
                do { try p.run() } catch { cont.resume(returning: (false, "\(error)")); return }
                p.waitUntilExit()
                let o = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                let e = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                cont.resume(returning: (p.terminationStatus == 0, p.terminationStatus == 0 ? o : e))
            }
        }
    }

    private static func friendly(_ err: String) -> String {
        if err.contains("-1743") { return "I'm not allowed to control Spotify yet, allow Evie in Settings" }
        if err.contains("-600") { return "Spotify isn't open" }
        return "Spotify didn't do it"
    }

    // MARK: Calendar

    private func calendarAdd(_ a: [String: JSONValue]) -> HandsOutcome {
        guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else {
            return HandsOutcome(ok: false, detail: "Calendar access is off")
        }
        let iso = ISO8601DateFormatter()
        guard let title = a["title"]?.string, !title.isEmpty,
              let start = a["start"]?.string.flatMap(iso.date(from:)),
              let end = a["end"]?.string.flatMap(iso.date(from:)), end > start,
              let cal = store.defaultCalendarForNewEvents else {
            return HandsOutcome(ok: false, detail: "that event didn't make sense")
        }
        let e = EKEvent(eventStore: store)
        e.title = title
        e.startDate = start
        e.endDate = end
        e.isAllDay = a["all_day"]?.bool ?? false
        e.calendar = cal
        do {
            try store.save(e, span: .thisEvent)
        } catch {
            return HandsOutcome(ok: false, detail: "Calendar wouldn't save it")
        }
        return HandsOutcome(ok: true, detail: "added", data: ["id": e.eventIdentifier ?? "", "calendar": cal.title])
    }

    private func calendarDelete(_ id: String) -> HandsOutcome {
        guard let e = store.event(withIdentifier: id) else { return HandsOutcome(ok: false, detail: "event not found") }
        do {
            try store.remove(e, span: .thisEvent)
        } catch {
            return HandsOutcome(ok: false, detail: "Calendar wouldn't remove it")
        }
        return HandsOutcome(ok: true, detail: "removed")
    }
}
