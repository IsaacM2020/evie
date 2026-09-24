import EventKit
import Foundation

struct CalEventDTO: Encodable {
    var id: String = ""
    let title: String
    let start: Date
    let end: Date
    let allDay: Bool
    let calendar: String

    static func payload(_ events: [CalEventDTO], calendars: [CalInfo] = []) throws -> Data {
        try encoder.encode(Payload(events: events, calendars: calendars))
    }

    static func eventsJSON(_ events: [CalEventDTO]) -> String {
        (try? encoder.encode(events)).flatMap { String(data: $0, encoding: .utf8) } ?? "[]"
    }

    private struct Payload: Encodable {
        let events: [CalEventDTO]
        let calendars: [CalInfo]
    }

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        e.dateEncodingStrategy = .iso8601
        return e
    }()

    init(id: String = "", title: String, start: Date, end: Date, allDay: Bool, calendar: String) {
        self.id = id
        self.title = title
        self.start = start
        self.end = end
        self.allDay = allDay
        self.calendar = calendar
    }

    init(_ e: EKEvent) {
        self.init(id: e.eventIdentifier ?? "", title: e.title ?? "(no title)", start: e.startDate, end: e.endDate,
                  allDay: e.isAllDay, calendar: e.calendar?.title ?? "")
    }
}

// One calendar as Evie sees it. Pure, so the selftest can check which ones she reads.
struct CalInfo: Encodable, Equatable {
    let id: String
    let title: String
    let source: String
    let writable: Bool
    var used: Bool = false
    var account: String = ""  // the source's own id: two Google accounts can both be called "Google"
    var events: Int = 0
}

// Isaac's real calendars (Isaac, 2026-09-23): his Google "Isaac" calendar plus the Google
// Classroom ones. NOT the old timetable calendars (Math, Food, Commute, Class...: an IGCSE-era
// timetable, also in Google), not iCloud (old "Dipanjan"), not cubing or Family.
enum CalendarPick {
    static func isGoogle(_ source: String) -> Bool {
        let s = source.lowercased()
        return s.contains("google") || s.hasSuffix("@gmail.com")
    }

    // Classroom calendars are named after the class and year: "English A: Lang & Lit",
    // "Maths AA HL Class of 2028", "TOK Class of 2028", "Grade 11IB _Physics_2026-27".
    static func isClassroom(_ title: String) -> Bool {
        title.range(of: #"(?i)class of|\bib|ibdp|grade ?\d|\btok\b|english a\b|20\d\d"#, options: .regularExpression) != nil
    }

    static func wanted(_ c: CalInfo) -> Bool {
        isGoogle(c.source) && (c.title == "Isaac" || isClassroom(c.title))
    }

    /// Which calendars to read. If none match (Google signed out, renamed), read everything
    /// rather than go blind.
    static func read(_ cals: [CalInfo]) -> [CalInfo] {
        let w = cals.filter(wanted)
        return w.isEmpty ? cals : w
    }

    static func hasGoogle(_ cals: [CalInfo]) -> Bool { cals.contains { isGoogle($0.source) } }

    /// Where new events go: Google "Isaac" (shows on his phone). There can be two calendars called
    /// "Isaac"; the busy one is the real one. Never iCloud: nil means fail out loud.
    static func writeTarget(_ cals: [CalInfo]) -> CalInfo? {
        let g = cals.filter { isGoogle($0.source) && $0.writable }
        let isaac = g.filter { $0.title == "Isaac" }.max { $0.events < $1.events }
        return isaac ?? g.first { $0.title.lowercased().hasSuffix("@gmail.com") }
    }

    /// Each calendar with how many events it has from now over the next 30 days.
    static func infos(_ store: EKEventStore) -> [CalInfo] {
        let all = store.calendars(for: .event)
        let start = Date()
        let end = start.addingTimeInterval(30 * 86400)
        var counts: [String: Int] = [:]
        for e in store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: all)) {
            counts[e.calendar?.calendarIdentifier ?? "", default: 0] += 1
        }
        return all.map { c in
            var i = info(c)
            i.events = counts[c.calendarIdentifier] ?? 0
            return i
        }
    }

    static func info(_ c: EKCalendar) -> CalInfo {
        CalInfo(id: c.calendarIdentifier, title: c.title, source: c.source?.title ?? "",
                writable: c.allowsContentModifications, account: c.source?.sourceIdentifier ?? "")
    }
}

// The app owns Calendar access (macOS asks the app, not a background Python process), so it
// reads the next 14 days of Isaac's Google calendars from EventKit and pushes them to the core:
// on launch, every 5 minutes, and whenever the calendar changes.
@MainActor
final class CalendarFeed {
    static let days = 14
    private let store = EKEventStore()
    private let core: CoreClient
    private let onAccess: (Bool) -> Void
    private var observer: NSObjectProtocol?
    var onEvents: (([CalEventDTO]) -> Void)?  // the orb's "next up" chip

    init(core: CoreClient, onAccess: @escaping (Bool) -> Void) {
        self.core = core
        self.onAccess = onAccess
    }

    func run() async {
        let granted = (try? await store.requestFullAccessToEvents()) ?? false
        onAccess(granted)
        guard granted else { return }
        observer = NotificationCenter.default.addObserver(
            forName: .EKEventStoreChanged, object: store, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in await self?.push() }
        }
        while true {
            await push()
            try? await Task.sleep(for: .seconds(300))
        }
    }

    func push() async {
        // Without access EventKit returns no events, which would look like "nothing on".
        guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else { return }
        let start = Calendar.current.startOfDay(for: Date())
        guard let end = Calendar.current.date(byAdding: .day, value: Self.days, to: start) else { return }
        let all = store.calendars(for: .event)
        let infos = all.map(CalendarPick.info)
        let readIds = Set(CalendarPick.read(infos).map(\.id))
        let cals = all.filter { readIds.contains($0.calendarIdentifier) }
        let pred = store.predicateForEvents(withStart: start, end: end, calendars: cals)
        let raw = cals.isEmpty ? [] : store.events(matching: pred)
        let events = raw.map(CalEventDTO.init)
        onEvents?(events)
        var counts: [String: Int] = [:]
        for e in raw { counts[e.calendar?.calendarIdentifier ?? "", default: 0] += 1 }
        let listed = infos.map { c in
            var c = c
            c.used = readIds.contains(c.id)
            c.events = counts[c.id] ?? 0
            return c
        }
        guard let body = try? CalEventDTO.payload(events, calendars: listed) else { return }
        _ = await core.postCalendar(body)
    }
}
