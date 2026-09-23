import EventKit
import Foundation

struct CalEventDTO: Encodable {
    let title: String
    let start: Date
    let end: Date
    let allDay: Bool
    let calendar: String

    static func payload(_ events: [CalEventDTO]) throws -> Data {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        e.dateEncodingStrategy = .iso8601
        return try e.encode(["events": events])
    }
}

// The app owns Calendar access (macOS asks the app, not a background Python process), so it
// reads the next 8 days from EventKit and pushes them to the core: on launch, every 5 minutes,
// and whenever the calendar changes.
@MainActor
final class CalendarFeed {
    private let store = EKEventStore()
    private let core: CoreClient
    private let onAccess: (Bool) -> Void
    private var observer: NSObjectProtocol?

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
        guard let end = Calendar.current.date(byAdding: .day, value: 8, to: start) else { return }
        let pred = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        let events = store.events(matching: pred).map {
            CalEventDTO(title: $0.title ?? "(no title)", start: $0.startDate, end: $0.endDate,
                        allDay: $0.isAllDay, calendar: $0.calendar?.title ?? "")
        }
        guard let body = try? CalEventDTO.payload(events) else { return }
        _ = await core.postCalendar(body)
    }
}
