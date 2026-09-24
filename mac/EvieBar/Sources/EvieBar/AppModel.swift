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
    @Published var state = "idle" {  // idle | listening | thinking | speaking | working
        didSet {
            // After a turn the bubble stays open 4 s so Isaac can read what she heard and said.
            if state == "idle", ["thinking", "speaking"].contains(oldValue) { lingerBriefly() }
        }
    }
    @Published var heard = ""
    @Published var said = ""
    @Published var verdict = ""
    @Published var job: JobView? = nil
    @Published var lastJobSummary = ""
    @Published var axTrusted = AXIsProcessTrusted()
    @Published var micDenied = false
    // Phase 2: open mic + voice ID + pill
    @Published var earsMode: String? = nil  // nil = core has no ear models
    // Cached across launches, so a relaunch never shows "0 clips" before the core answers.
    @Published var voiceprint = AppModel.cachedVoiceprint() {
        didSet {
            UserDefaults.standard.set([Double(voiceprint.clips), voiceprint.seconds, voiceprint.ready ? 1 : 0],
                                      forKey: "voiceprint")
        }
    }

    static func cachedVoiceprint() -> VoicePrintDTO {
        guard let v = UserDefaults.standard.array(forKey: "voiceprint") as? [Double], v.count == 3 else {
            return VoicePrintDTO(clips: 0, seconds: 0, ready: false)
        }
        return VoicePrintDTO(clips: Int(v[0]), seconds: v[1], ready: v[2] == 1)
    }
    @Published var enrolling = false
    @Published var enrollStartClips = 0
    @Published var shadowLog: [ShadowRow] = []
    @Published var pillNote = ""
    @Published var recording: Bool? = nil  // nil: the core has no open mic, so no recorder
    @Published var showPill = UserDefaults.standard.object(forKey: "showPill") as? Bool ?? true
    // The orb (Orb.swift)
    @Published var orbSide: OrbSide = .right
    @Published var orbHover = false
    @Published var bubbleHover = false
    @Published var linger = false
    @Published var micLevel: Float = 0
    @Published var voiceLevel: Float = 0
    @Published var showWork = UserDefaults.standard.object(forKey: "showWork") as? Bool ?? true
    var clickTalkEnabled = true  // the selftest turns it off so a synthetic click never opens the mic
    private var clickTalking = false
    private var lastSettingsSync: Date?
    private var lingerTask: Task<Void, Never>?

    private let core = CoreClient()
    private var calendarFeed: CalendarFeed?
    private var eventFeed: EventFeed?
    private var ptt = PushToTalk()
    private var chord = Chord()
    private let recorder = Recorder()
    private var monitors: [Any] = []
    private let ears = Ears()
    private let pill = OrbController()
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
        ears.onVoiceLevel = { [weak self] lvl in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.voiceLevel = lvl } }
        }
        ears.onMicLevel = { [weak self] lvl in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, self.state == "listening" else { return }
                    self.micLevel = self.micLevel * 0.5 + lvl * 0.5  // smoothed, so the bars don't flicker
                }
            }
        }
        if showPill { pill.show(self) }
    }

    var iconState: String {
        MenuIcon.state(online: online, state: state, working: job != nil, jevOk: jevOk)
    }

    // MARK: what the orb shows

    /// Idle with a job running shows as working (the progress ring).
    var orbState: String { state == "idle" && job != nil ? "working" : state }

    var orbExpanded: Bool {
        orbHover || bubbleHover || linger || ["listening", "thinking", "speaking"].contains(state) || !pillNote.isEmpty
    }

    var youLine: String? {
        if state == "listening" { return nil }
        return heard.isEmpty || heard.hasPrefix("(") ? nil : heard
    }

    var evieLine: String? {
        switch state {
        case "listening": return clickTalking ? "Listening… click to send" : "Listening…"
        case "thinking": return "Thinking…"
        default:
            if !pillNote.isEmpty { return pillNote }
            if !said.isEmpty { return said }
            return job == nil ? (heard.isEmpty ? "Hold ⌃⌥ or click me to talk" : nil) : nil
        }
    }

    var stepLine: String? {
        guard let j = job else { return nil }
        return j.lines.last.map { "\(j.goal): \($0)" } ?? j.goal
    }

    /// k of n from the job's plan (Phase 3c T12); nil until known.
    @Published var jobProgress: Double? = nil

    var stopVisible: Bool { job != nil || state == "speaking" || state == "working" }

    private func lingerBriefly() {
        linger = true
        lingerTask?.cancel()
        lingerTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            if !Task.isCancelled { self?.linger = false }
        }
    }

    /// Click the orb to talk, click again to send (the same path as holding ⌃⌥).
    func orbClick() {
        guard clickTalkEnabled else { return }
        if clickTalking {
            clickTalking = false
            runPTT(.keyUp(at: ProcessInfo.processInfo.systemUptime))
        } else if state != "listening" {
            clickTalking = true
            let t = ProcessInfo.processInfo.systemUptime
            runPTT(.keyDown(at: t - 1))  // a click has no "hold", so it never counts as a quick tap
        }
    }

    func stopAll() async {
        clickTalking = false
        await core.stopAll()
    }

    func setShowWork(_ on: Bool) {
        showWork = on
        UserDefaults.standard.set(on, forKey: "showWork")
        Task { await core.settings(showWork: on) }
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
        if want && !ears.streaming {
            if !ears.start(streaming: true) { error = "Couldn't start the open mic." }
        } else if !want && ears.streaming {
            ears.stopStreaming()
            if state != "listening" { ears.stop() }
        }
    }

    @discardableResult
    func setEarsMode(_ mode: String) async -> CoreRefusal? {
        let old = earsMode
        earsMode = mode  // instant in the UI, rolled back if the core refuses
        var refusal: CoreRefusal?
        switch await core.setEarsMode(mode) {
        case .success(let e):
            applyEars(e)
            error = nil
        case .failure(let r):
            earsMode = old
            error = r.detail
            refusal = r
        }
        syncEars()
        return refusal
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

    // MARK: hold left ⌃⌥ to talk, left ⌃⌥⌘ for Live

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
        guard e.type == .flagsChanged else {
            // A real key while the talk chord is held: that was a ⌃⌥ shortcut, not speech.
            if chord.talking { runPTT(.otherKey) }
            return
        }
        for out in chord.update(raw: UInt(e.modifierFlags.rawValue)) {
            switch out {
            case .talkDown: runPTT(.keyDown(at: e.timestamp))
            case .talkUp: runPTT(.keyUp(at: e.timestamp))
            case .talkCancel: runPTT(.otherKey)
            case .liveToggle: Task { await toggleLive() }
            }
        }
    }

    /// Left ⌃⌥⌘: Live open mic on, or off again.
    func toggleLive() async {
        let target = earsMode == "live" ? "off" : "live"
        var refusal: CoreRefusal?
        let until = Date().addingTimeInterval(10)
        repeat {
            refusal = await setEarsMode(target)
            guard let r = refusal, LiveSwitch.shouldRetry(status: r.status), Date() < until else { break }
            note("Starting up…", for: 2)
            try? await Task.sleep(for: .seconds(1))
        } while true
        if refusal == nil {
            (target == "live" ? Earcon.liveOn : Earcon.liveOff).play()
            note(target == "live" ? "Live mic on" : "Live mic off", for: 3)
        } else if let r = refusal {
            note(LiveSwitch.note(status: r.status, detail: r.detail, clipsLeft: max(0, 8 - voiceprint.clips)), for: 5)
        }
    }

    private var pttViaEars = false
    private var earsWarmStop: Task<Void, Never>?

    private func runPTT(_ input: PTTInput) {
        switch ptt.handle(input) {
        case .startRecording:
            guard online, !micDenied else { ptt = PushToTalk(); return }
            // The talk key records through the open mic's engine: 0.4 s from before the press is
            // kept (no clipped first word) and it's the same audio voice ID compares against.
            earsWarmStop?.cancel()
            if ears.start(streaming: ears.streaming) {
                ears.beginCapture()
                pttViaEars = true
            } else {
                guard recorder.start() else { ptt = PushToTalk(); return }
                pttViaEars = false
            }
            Earcon.listening.play()
            state = "listening"
            Task { await core.voiceStart() }
            if !pttViaEars {
                Task { [weak self] in  // the orb's bars follow his voice while he talks
                    while let self, self.state == "listening" {
                        self.micLevel = self.recorder.level()
                        try? await Task.sleep(for: .milliseconds(50))
                    }
                    self?.micLevel = 0
                }
            }
        case .stopAndSend:
            Earcon.gotIt.play()
            state = "thinking"
            micLevel = 0
            if pttViaEars {
                let channel = ears.echoCancel ? "live" : "raw"
                ears.endCapture { [weak self] wav in
                    Task { @MainActor in
                        guard let self else { return }
                        guard let wav else { self.state = "idle"; return }
                        if await self.core.voice(wav, channel: channel) == nil { self.error = "Couldn't reach Evie's core." }
                        self.coolEars()
                    }
                }
            } else {
                guard let wav = recorder.stop() else { state = "idle"; return }
                Task {
                    if await core.voice(wav) == nil { error = "Couldn't reach Evie's core." }
                }
            }
        case .cancel:
            if pttViaEars { ears.cancelCapture(); coolEars() } else { recorder.cancel() }
            state = job == nil ? "idle" : "working"
            micLevel = 0
        case .none:
            break
        }
    }

    /// With the open mic off, the engine started for the talk key stays warm 30 s (a quick second
    /// question starts instantly), then stops so the mic isn't on for nothing.
    private func coolEars() {
        guard !ears.streaming else { return }
        earsWarmStop?.cancel()
        earsWarmStop = Task { [weak self] in
            try? await Task.sleep(for: .seconds(30))
            guard let self, !Task.isCancelled, !self.ears.streaming, self.state != "listening" else { return }
            self.ears.stop()
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
        case "step":  // a screen task's current step, on the orb
            if let t = ev.text, !t.isEmpty { note(t, for: 6) }
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
        let wasOnline = online
        online = s != nil
        jevOk = s?.jevOk
        if let s {
            if !wasOnline || lastSettingsSync == nil {  // a (re)started core learns the orb's settings
                await core.settings(showWork: showWork)
                lastSettingsSync = Date()
            }
            earsMode = s.earsMode
            if let v = s.voiceprint { voiceprint = v }
            recording = s.earsMode == nil ? nil : await core.recorder()
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

    func setRecording(_ on: Bool) async {
        recording = await core.recorder(set: on) ?? recording
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

// Instant, local feedback on the key itself: nothing waits on the network or the core.
enum Earcon {
    case listening, gotIt, liveOn, liveOff

    private static let sounds: [Earcon: NSSound] = {
        var m: [Earcon: NSSound] = [:]
        if let s = NSSound(named: "Tink") { s.volume = 0.25; m[.listening] = s }
        if let s = NSSound(named: "Pop") { s.volume = 0.3; m[.gotIt] = s }
        if let s = NSSound(named: "Hero") { s.volume = 0.3; m[.liveOn] = s }
        if let s = NSSound(named: "Bottle") { s.volume = 0.3; m[.liveOff] = s }
        return m
    }()

    func play() {
        guard let s = Earcon.sounds[self] else { return }
        s.stop()
        s.play()
    }
}
