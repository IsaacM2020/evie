import AppKit
import Combine
import Foundation
import ServiceManagement

// ObservableObject + @Published instead of @Observable/@State: those are macros, and macro
// plugins only ship with full Xcode. This app builds with the Command Line Tools alone.
@MainActor
final class AppModel: ObservableObject {
    @Published var online = false
    @Published var jevOk: Bool? = nil
    @Published var input = ""
    @Published var speaker = "isaac"
    @Published var inCall = false
    @Published var busy = false
    @Published var last: OutcomeDTO? = nil
    @Published var lastUtterance = ""
    @Published var error: String? = nil
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled
    @Published var calendarDenied = false
    private let core = CoreClient()
    private var calendarFeed: CalendarFeed?

    init() {
        Task { await pollForever() }
        let feed = CalendarFeed(core: core) { [weak self] granted in self?.calendarDenied = !granted }
        calendarFeed = feed
        Task { await feed.run() }
    }

    func openCalendarSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars") {
            NSWorkspace.shared.open(url)
        }
    }

    var iconName: String {
        if !online { return "waveform.slash" }
        if jevOk == false { return "exclamationmark.triangle" }
        return "waveform"
    }

    func pollForever() async {
        while true {
            await refresh()
            try? await Task.sleep(for: .seconds(5))
        }
    }

    func refresh() async {
        let s = await core.status()
        online = s != nil
        jevOk = s?.jevOk
    }

    func send() async {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        busy = true
        error = nil
        defer { busy = false }
        switch await core.decide(utterance: text, speaker: speaker, inCall: inCall) {
        case .success(let o):
            last = o
            lastUtterance = text
            input = ""
        case .failure:
            error = "Can't reach Evie's core. Is it running?"
            online = false
        }
        await refresh()
    }

    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            self.error = "Couldn't change Open at login"
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }
}
