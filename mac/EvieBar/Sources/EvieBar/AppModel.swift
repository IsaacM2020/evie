import AppKit
import ApplicationServices
import Combine
import Foundation
import ServiceManagement

struct JobView: Equatable {
    let id: String
    let goal: String
    let started: Date
    var lines: [String]
}

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
    // Phase 1: live voice state
    @Published var state = "idle"  // idle | listening | thinking | speaking | working
    @Published var heard = ""
    @Published var said = ""
    @Published var verdict = ""
    @Published var job: JobView? = nil
    @Published var lastJobSummary = ""
    @Published var axTrusted = AXIsProcessTrusted()
    @Published var micDenied = false
    // Phase 2: open mic + voice ID + pill
    @Published var earsMode: String? = nil  // nil = core has no ear models
    @Published var voiceprint = VoicePrintDTO(clips: 0, seconds: 0, ready: false)
    @Published var enrolling = false
    @Published var enrollStartClips = 0
    @Published var shadowLog: [ShadowRow] = []
    @Published var pillNote = ""
    @Published var showPill = UserDefaults.standard.object(forKey: "showPill") as? Bool ?? true

    private let core = CoreClient()
    private var calendarFeed: CalendarFeed?
    private var eventFeed: EventFeed?
    private var ptt = PushToTalk()
    private let recorder = Recorder()
    private var monitors: [Any] = []
    private let ears = Ears()
    private let pill = PillController()
    private let hands = Hands()
    private var noteClear: Task<Void, Never>?

    /// preview: true builds a model with no side effects (no mic, keys, network) for snapshots.
    init(preview: Bool = false) {
        guard !preview else { return }
        Task { await pollForever() }
        let feed = CalendarFeed(core: core) { [weak self] granted in self?.calendarDenied = !granted }
        calendarFeed = feed
        Task { await feed.run() }
        let events = EventFeed { [weak self] ev in self?.apply(ev) }
        eventFeed = events
        Task { await events.run() }
        Task { micDenied = !(await Recorder.requestMic()) }
        installKeyMonitors()
        if showPill { pill.show(self) }
    }

    var iconState: String {
        MenuIcon.state(online: online, state: state, working: job != nil, jevOk: jevOk)
    }

    /// The one line the floating pill shows; nil = just the orb.
    var pillLine: String? {
        switch state {
        case "listening": return "Listening…"
        case "thinking": return heard.isEmpty || heard.hasPrefix("(") ? "Thinking…" : heard
        case "speaking": return said.isEmpty ? nil : said
        default: return pillNote.isEmpty ? nil : pillNote
        }
    }

    private func note(_ text: String, for seconds: Double = 5) {
        pillNote = text
        noteClear?.cancel()
        noteClear = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            if !Task.isCancelled { self?.pillNote = "" }
        }
    }

    func setShowPill(_ on: Bool) {
        showPill = on
        UserDefaults.standard.set(on, forKey: "showPill")
        if on { pill.show(self) } else { pill.hide() }
    }

    // MARK: open mic

    private func syncEars() {
        let want = online && !micDenied && (earsMode ?? "off") != "off"
        if want && !ears.running {
            if !ears.start() { error = "Couldn't start the open mic." }
        } else if !want && ears.running {
            ears.stop()
        }
    }

    func setEarsMode(_ mode: String) async {
        let old = earsMode
        earsMode = mode  // instant in the UI, rolled back if the core refuses
        switch await core.setEarsMode(mode) {
        case .success(let e):
            applyEars(e)
            error = nil
        case .failure(let r):
            earsMode = old
            error = r.detail
        }
        syncEars()
    }

    func toggleEnroll() async {
        if case .success(let e) = await core.enroll(!enrolling) {
            if e.enrolling { enrollStartClips = e.voiceprint.clips }
            applyEars(e)
        }
    }

    private func applyEars(_ e: EarsDTO) {
        earsMode = e.mode
        enrolling = e.enrolling
        voiceprint = e.voiceprint
    }

    // MARK: hold Fn to talk

    private func installKeyMonitors() {
        // Reading keys in other apps needs Accessibility; this asks once (the grant sticks because
        // the app is signed with a stable Apple Development certificate).
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        axTrusted = AXIsProcessTrustedWithOptions(opts)
        let handler: (NSEvent) -> Void = { [weak self] e in
            MainActor.assumeIsolated { self?.handleKey(e) }
        }
        if let g = NSEvent.addGlobalMonitorForEvents(matching: [.flagsChanged, .keyDown], handler: handler) {
            monitors.append(g)
        }
        if let l = NSEvent.addLocalMonitorForEvents(matching: [.flagsChanged, .keyDown], handler: { e in
            handler(e)
            return e
        }) {
            monitors.append(l)
        }
    }

    private func handleKey(_ e: NSEvent) {
        let input: PTTInput
        if e.type == .flagsChanged && e.keyCode == 63 {  // 63 = Fn / Globe
            input = e.modifierFlags.contains(.function) ? .fnDown(at: e.timestamp) : .fnUp(at: e.timestamp)
        } else {
            input = .otherKey
        }
        switch ptt.handle(input) {
        case .startRecording:
            guard online, !micDenied, recorder.start() else { ptt = PushToTalk(); return }
            Earcon.listening.play()
            state = "listening"
            Task { await core.voiceStart() }
        case .stopAndSend:
            guard let wav = recorder.stop() else { state = "idle"; return }
            Earcon.gotIt.play()
            state = "thinking"
            Task {
                if await core.voice(wav) == nil { error = "Couldn't reach Evie's core." }
            }
        case .cancel:
            recorder.cancel()
            state = job == nil ? "idle" : "working"
        case .none:
            break
        }
    }

    // MARK: live events from the core

    private func apply(_ ev: CoreEvent) {
        switch ev.kind {
        case "hello":
            if let j = ev.job {
                job = JobView(id: j.id, goal: j.goal, started: Date(timeIntervalSince1970: j.started), lines: j.events)
            } else {
                job = nil
            }
        case "heard":
            heard = (ev.text ?? "").isEmpty ? "(didn't catch that)" : (ev.text ?? "")
            said = ""
            verdict = ""
        case "verdict":
            verdict = [ev.action, ev.reason].compactMap { $0 }.joined(separator: " · ")
        case "say":
            said = ev.text ?? ""
        case "state":
            if state == "listening" { break }  // Fn is held: the key, not the core, ends listening
            state = ev.state ?? state
        case "job_started":
            job = JobView(id: ev.id ?? "", goal: ev.goal ?? "", started: Date(), lines: [])
            lastJobSummary = ""
        case "job_event":
            if var j = job, j.id == ev.id, let line = ev.line {
                j.lines = Array((j.lines + [line]).suffix(5))
                job = j
                note(line)
            }
        case "job_done":
            if job?.id == ev.id { job = nil }
            lastJobSummary = ev.summary ?? ""
            if !lastJobSummary.isEmpty { note(lastJobSummary, for: 8) }
        case "shadow":
            guard let would = ev.would, !would.hasPrefix("ignore") else { break }
            shadowLog = Array(([ShadowRow(text: ev.text ?? "", would: would)] + shadowLog).prefix(10))
            note("Would have: \(would)")
        case "voiceprint":
            if let c = ev.clips, let sec = ev.seconds, let r = ev.ready {
                voiceprint = VoicePrintDTO(clips: c, seconds: sec, ready: r)
            }
        case "do":
            guard let id = ev.id else { break }
            Task {
                if let outcome = await hands.run(ev) { await core.handsResult(id: id, outcome) }
            }
        case "ears":
            if let m = ev.mode { earsMode = m }
            if let e = ev.enrolling { enrolling = e }
            if let v = ev.voiceprint { voiceprint = v }
            syncEars()
        default:
            break
        }
    }

    // MARK: status, typed input, settings

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
        if let s {
            earsMode = s.earsMode
            if let v = s.voiceprint { voiceprint = v }
        }
        syncEars()
        let trusted = AXIsProcessTrusted()
        if trusted && !axTrusted {  // just granted: monitors added before the grant stay deaf
            monitors.forEach { NSEvent.removeMonitor($0) }
            monitors = []
            installKeyMonitors()
        }
        axTrusted = trusted
        // A restarted core has no calendar yet: push one now instead of waiting 5 minutes.
        if s?.calendarFresh == false, !calendarDenied {
            await calendarFeed?.push()
        }
    }

    func send() async {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        busy = true
        error = nil
        defer { busy = false }
        if await core.hear(text, speaker: speaker) != nil {
            input = ""
        } else {
            error = "Can't reach Evie's core. Is it running?"
            online = false
        }
    }

    func dryRun() async {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        busy = true
        defer { busy = false }
        switch await core.decide(utterance: text, speaker: speaker, inCall: inCall) {
        case .success(let o):
            last = o
            lastUtterance = text
        case .failure:
            error = "Can't reach Evie's core. Is it running?"
        }
    }

    func stopJob() async {
        await core.stopJob()
    }

    func openSettings(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
            NSWorkspace.shared.open(url)
        }
    }

    func openCalendarSettings() { openSettings("Privacy_Calendars") }

    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            self.error = "Couldn't change Open at login"
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }
}

// Instant, local feedback on the key itself: nothing waits on the network or the core.
enum Earcon {
    case listening, gotIt

    private static let sounds: [Earcon: NSSound] = {
        var m: [Earcon: NSSound] = [:]
        if let s = NSSound(named: "Tink") { s.volume = 0.25; m[.listening] = s }
        if let s = NSSound(named: "Pop") { s.volume = 0.3; m[.gotIt] = s }
        return m
    }()

    func play() {
        guard let s = Earcon.sounds[self] else { return }
        s.stop()
        s.play()
    }
}
