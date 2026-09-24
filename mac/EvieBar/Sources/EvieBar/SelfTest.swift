import AppKit
import AVFoundation
import Foundation

final class StatusBox: @unchecked Sendable {
    let sema = DispatchSemaphore(value: 0)
    var result: CoreStatus?
}

enum SelfTest {
    static let outcome = #"{"action":"clarify","reason":"missing detail","followup":false,"decision":{"for_evie":0.8,"route":"quick_action","route_confidence":1.0,"route_probs":{"quick_action":1.0},"complete":0.1,"has_event":0.02,"latency_ms":332.4,"cost_usd":0.00002}}"#
    static let status = #"{"ok":true,"version":"0.1.0","jev_ok":null}"#

    static func run() -> Bool {
        var ok = true
        func check(_ cond: Bool, _ name: String) {
            print(cond ? "PASS" : "FAIL", name)
            ok = ok && cond
        }
        let o = try? CoreJSON.decoder.decode(OutcomeDTO.self, from: Data(outcome.utf8))
        check(o?.action == "clarify" && o?.decision?.forEvie == 0.8 && o?.decision?.latencyMs == 332.4,
              "decode outcome")
        let s = try? CoreJSON.decoder.decode(CoreStatus.self, from: Data(status.utf8))
        check(s?.ok == true && s?.jevOk == nil, "decode status with null jev_ok")
        let ev = CalEventDTO(title: "Math", start: Date(timeIntervalSince1970: 1_790_211_600),
                             end: Date(timeIntervalSince1970: 1_790_215_200), allDay: false, calendar: "Math")
        let body = (try? CalEventDTO.payload([ev])).flatMap { String(data: $0, encoding: .utf8) } ?? ""
        check(body.contains(#""all_day":false"#) && body.contains(#""start":"2026-09-24T01:00:00Z""#),
              "calendar payload is snake_case with UTC ISO dates")
        let cals = [CalInfo(id: "1", title: "Home", source: "iCloud", writable: true),
                    CalInfo(id: "2", title: "Isaac", source: "Google", writable: true),
                    CalInfo(id: "3", title: "2026-28 Chemistry", source: "Google", writable: false),
                    CalInfo(id: "4", title: "Dipanjan stuff", source: "iCloud", writable: true),
                    CalInfo(id: "5", title: "Chemistry", source: "Google", writable: true),
                    CalInfo(id: "6", title: "Isaac M Cubing", source: "Google", writable: true),
                    CalInfo(id: "7", title: "TOK Class of 2028", source: "Google", writable: false),
                    CalInfo(id: "8", title: "English A: Lang & Lit", source: "Google", writable: false),
                    CalInfo(id: "9", title: "Grade 11IB _Physics_2026-27", source: "Google", writable: false),
                    CalInfo(id: "10", title: "Food", source: "Google", writable: true)]
        check(CalendarPick.read(cals).map(\.id) == ["2", "3", "7", "8", "9"] && CalendarPick.writeTarget(cals)?.id == "2",
              "calendar: Isaac + Classroom only (no old timetable, cubing, iCloud), writes to Google Isaac")
        var twins = cals
        twins.append(CalInfo(id: "11", title: "Isaac", source: "Google", writable: true, events: 26))
        check(CalendarPick.writeTarget(twins)?.id == "11", "calendar: of two 'Isaac' calendars, writes to the busy one")
        check(CalendarPick.writeTarget(cals.filter { $0.source == "iCloud" }) == nil
              && CalendarPick.writeTarget(cals.filter { $0.title == "Food" || $0.source == "iCloud" }) == nil
              && CalendarPick.read(cals.filter { $0.source == "iCloud" }).count == 2,
              "calendar: no Google means no writes (never iCloud), reads stay on")
        let withId = (try? CalEventDTO.payload([CalEventDTO(id: "e1", title: "Sax", start: Date(timeIntervalSince1970: 0),
                                                            end: Date(timeIntervalSince1970: 60), allDay: false,
                                                            calendar: "Isaac")], calendars: cals))
            .flatMap { String(data: $0, encoding: .utf8) } ?? ""
        check(withId.contains(#""id":"e1""#) && withId.contains(#""calendars":["#), "calendar: payload has ids + calendars")
        func ptt(_ inputs: [PTTInput]) -> [PTTAction] {
            var p = PushToTalk()
            return inputs.map { p.handle($0) }
        }
        check(ptt([.keyDown(at: 0), .keyUp(at: 0.1)]) == [.startRecording, .cancel], "ptt: quick tap cancels")
        check(ptt([.keyDown(at: 0), .keyUp(at: 1.2)]) == [.startRecording, .stopAndSend], "ptt: hold sends")
        check(ptt([.keyDown(at: 0), .otherKey, .keyUp(at: 1)]) == [.startRecording, .cancel, .none],
              "ptt: Fn+arrow cancels, release does nothing")
        check(ptt([.keyDown(at: 0), .keyDown(at: 0.5), .keyUp(at: 1)]) == [.startRecording, .none, .stopAndSend],
              "ptt: repeated down ignored")
        check(ptt([.keyUp(at: 1), .otherKey]) == [.none, .none], "ptt: up/other without down do nothing")
        check(ptt([.keyDown(at: 0), .keyUp(at: 0.25)]) == [.startRecording, .stopAndSend], "ptt: exactly 0.25s sends")
        func chord(_ raws: [UInt]) -> [[Chord.Out]] {
            var c = Chord()
            return raws.map { c.update(raw: $0) }
        }
        let lc = Chord.leftControl, lo = Chord.leftOption, lcmd = Chord.leftCommand
        check(chord([lc, lc | lo, lc, 0]) == [[], [.talkDown], [.talkUp], []], "chord: left ⌃⌥ held = talk")
        check(chord([lc | lo, lc | lo | lcmd, lc | lo, 0, lc | lo]) == [[.talkDown], [.talkCancel, .liveToggle], [], [], [.talkDown]],
              "chord: ⌃⌥⌘ cancels the talk, toggles live once, no talk until released")
        check(chord([lc | lo | lcmd, lc | lo | lcmd, 0, lc | lo | lcmd]) == [[.liveToggle], [], [], [.liveToggle]],
              "chord: live toggles once per press")
        check(chord([lc | 0x40, 0x2000 | lo]) == [[], []], "chord: right ⌥ (Ripple) or right ⌃ never trigger")
        let jobEv = try? CoreJSON.decoder.decode(CoreEvent.self, from: Data(
            #"{"kind":"job_event","t":1.5,"id":"ab12","line":"Ran: pytest","extra":[1,2]}"#.utf8))
        check(jobEv?.kind == "job_event" && jobEv?.line == "Ran: pytest" && jobEv?.id == "ab12", "decode job_event")
        let st = try? CoreJSON.decoder.decode(CoreStatus.self, from: Data(
            #"{"ok":true,"version":"0.1.0","jev_ok":true,"stt_ready":true,"voice_ready":true,"calendar_fresh":false,"job":null}"#.utf8))
        check(st?.calendarFresh == false && st?.sttReady == true, "decode phase 1 status")
        var packer = FramePacker()
        let first = packer.add([Int16](repeating: 7, count: 1000))
        let second = packer.add([Int16](repeating: 7, count: 24))
        check(first.count == 1 && first[0].count == 1024 && packer.pending.isEmpty && second.count == 1,
              "ears: 512-sample int16 frames, remainder carried over")
        if let fmt = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1),
           let b = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: 4) {
            b.frameLength = 4
            for i in 0..<4 { b.floatChannelData![0][i] = Float(i) }
            let c = AudioCopy.copy(b)
            b.floatChannelData![0][0] = 99  // the engine reusing its buffer must not touch the copy
            check(c?.frameLength == 4 && c?.floatChannelData?[0][0] == 0 && c?.floatChannelData?[0][3] == 3,
                  "ears: tap buffer is copied before the async hop")
        }
        if let vf = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1) {
            let data = [Float](repeating: 0.25, count: 480).withUnsafeBufferPointer { Data(buffer: $0) }
            let vb = AudioCopy.floats(data, format: vf)
            check(vb?.frameLength == 480 && vb?.floatChannelData?[0][479] == 0.25, "mouth: float32 bytes become a buffer")
        }
        check(AXPick.label(title: "", desc: "Send", value: nil, help: nil, placeholder: nil, role: "AXButton") == "Send"
              && AXPick.label(title: nil, desc: nil, value: "typed secret", help: nil, placeholder: "Search", role: "AXTextField") == "Search",
              "eyes: labels come from title/description, a field's typed text is never its label")
        check(AXPick.keep(role: "AXButton", label: "Play", width: 20, height: 20)
              && AXPick.keep(role: "AXTextField", label: "", width: 100, height: 20)
              && !AXPick.keep(role: "AXButton", label: "", width: 20, height: 20)
              && !AXPick.keep(role: "AXButton", label: "Hidden", width: 0, height: 0)
              && !AXPick.keep(role: "AXGroup", label: "Sidebar", width: 200, height: 400), "eyes: keeps only real, labelled controls")
        check(KeyMap.parse("cmd+t").map { $0.0 == 17 && $0.1 == .maskCommand } == true
              && KeyMap.parse("cmd+shift+n").map { $0.0 == 45 && $0.1.contains(.maskShift) } == true
              && KeyMap.parse("return").map { $0.0 == 36 } == true && KeyMap.parse("cmd+hyper") == nil, "eyes: key combos")
        check(WebReader.json("a\"b</script>") == #""a\"b<\/script>""# || WebReader.json("a\"b</script>") == #""a\"b</script>""#,
              "eyes: text for a page script is JSON-escaped")
        check(MenuIcon.states.allSatisfy { MenuIcon.image($0).isTemplate }, "menu icon: template image per state")
        check(MenuIcon.state(online: false, state: "idle", working: true, jevOk: true) == "offline"
              && MenuIcon.state(online: true, state: "listening", working: true, jevOk: true) == "listening"
              && MenuIcon.state(online: true, state: "idle", working: true, jevOk: false) == "working",
              "menu icon: state priority")
        let shadow = try? CoreJSON.decoder.decode(CoreEvent.self, from: Data(
            #"{"kind":"shadow","text":"play lofi","speaker":"isaac","would":"act · quick_action"}"#.utf8))
        check(shadow?.would == "act · quick_action" && shadow?.speaker == "isaac", "decode shadow event")
        let earsEv = try? CoreJSON.decoder.decode(CoreEvent.self, from: Data(
            #"{"kind":"ears","mode":"shadow","enrolling":false,"voiceprint":{"clips":3,"seconds":6.1,"ready":false}}"#.utf8))
        check(earsEv?.mode == "shadow" && earsEv?.voiceprint?.clips == 3, "decode ears event")
        let st2 = try? CoreJSON.decoder.decode(CoreStatus.self, from: Data(
            #"{"ok":true,"version":"0.1.0","jev_ok":true,"ears_mode":"off","voiceprint":{"clips":0,"seconds":0,"ready":false}}"#.utf8))
        check(st2?.earsMode == "off" && st2?.voiceprint?.ready == false, "decode phase 2 status")
        var gate = HandsGate()
        check(gate.admit(id: "a", expires: 100, now: 50) && !gate.admit(id: "a", expires: 100, now: 51)
              && !gate.admit(id: "b", expires: 100, now: 101), "hands: each command once, never after expiry")
        check(HandsGate.isSpotifyURI("spotify:track:4uLU6hMCjMI75M1A2tKUQC")
              && !HandsGate.isSpotifyURI("spotify:track:x\" to quit")
              && !HandsGate.isSpotifyURI("https://evil"), "hands: only clean Spotify links reach AppleScript")
        let doEv = try? CoreJSON.decoder.decode(CoreEvent.self, from: Data(
            #"{"kind":"do","id":"c1","op":"calendar_add","args":{"title":"Dentist","all_day":false,"n":3},"expires":1.5}"#.utf8))
        check(doEv?.op == "calendar_add" && doEv?.args?["title"]?.string == "Dentist"
              && doEv?.args?["all_day"]?.bool == false && doEv?.expires == 1.5, "decode do command")
        check(MouthGate.onStart(playing: nil, new: "b") == .play
              && MouthGate.onStart(playing: "a", new: "b") == .flushOldThenPlay
              && MouthGate.onStart(playing: "b", new: "b") == .play, "mouth: one voice at a time, a new line flushes the old")
        check(LiveSwitch.shouldRetry(status: 503) && LiveSwitch.shouldRetry(status: 0)
              && !LiveSwitch.shouldRetry(status: 409) && !LiveSwitch.shouldRetry(status: 400),
              "live: a warming or unreachable core is retried, a real refusal isn't")
        check(LiveSwitch.note(status: 409, detail: "x", clipsLeft: 3).contains("3 more times")
              && !LiveSwitch.note(status: 503, detail: "x", clipsLeft: 8).contains("more times")
              && !LiveSwitch.note(status: 0, detail: "x", clipsLeft: 8).contains("more times"),
              "live: only a real 409 asks Isaac to train his voice")
        var cap = CaptureBuffer(rate: 16000, keep: 1.0)
        cap.add([Int16](repeating: 1, count: 16000))  // 1 s before the key: only 0.4 s is kept
        cap.begin(preroll: 0.4)
        cap.add([Int16](repeating: 2, count: 8000))
        let clip = cap.end() ?? []
        check(clip.count == 6400 + 8000 && clip.first == 1 && clip.last == 2 && cap.end() == nil,
              "talk key: 0.4 s from before the key press, then everything until release")
        let w = WAV.encode([1, -1, 300], rate: 16000)
        check(w.count == 44 + 6 && String(data: w.prefix(4), encoding: .ascii) == "RIFF"
              && w[24] == 0x80 && w[25] == 0x3E && w[34] == 16, "talk key: 16 kHz 16-bit mono WAV")
        check(AppleRun.quote(#"say "hi" \ bye"#) == #""say \"hi\" \\ bye""#,
              "safari: page scripts and messages can't break out of the AppleScript string")
        check(!PageSettle.done(ready: "complete", url: "https://y.com/a", before: "https://y.com/a", quietMs: 900)
              && !PageSettle.done(ready: "complete", url: "https://y.com/b", before: "https://y.com/a", quietMs: 100)
              && PageSettle.done(ready: "complete", url: "https://y.com/b", before: "https://y.com/a", quietMs: 350)
              && PageSettle.done(ready: "complete", url: "https://y.com/a", before: "", quietMs: 300),
              "safari: a page is ready when it's loaded, on the new address and quiet for 300 ms")
        check(WebTab(window: 42, index: 3).ref == "tab 3 of window id 42", "safari: page ops go to Evie's own tab")
        // The capsule and its card (Phase 4 look): snapping, spring, layout, taps, what shows when.
        let screen = NSRect(x: 0, y: 0, width: 1440, height: 900)
        let flick = OrbSnap.rest(center: CGPoint(x: 600, y: 450), velocity: CGVector(dx: 1500, dy: 0), in: screen)
        let still = OrbSnap.rest(center: CGPoint(x: 600, y: 450), velocity: .zero, in: screen)
        let high = OrbSnap.rest(center: CGPoint(x: 1300, y: 890), velocity: CGVector(dx: 0, dy: 2000), in: screen)
        check(flick.side == .right && still.side == .left && high.side == .right
              && high.center.y <= screen.maxY - OrbGeometry.edge - OrbGeometry.capH / 2
              && abs(still.center.x - (OrbGeometry.edge + OrbGeometry.capW / 2)) < 0.01,
              "capsule: a flick throws it to the far edge, a slow drop snaps to the nearer one, always on screen")
        var x: CGFloat = 0, v: CGFloat = 0, peak: CGFloat = 0
        for _ in 0..<120 {
            (x, v) = Spring().step(x: x, v: v, target: 100, dt: 1.0 / 120)
            peak = max(peak, x)
        }
        check(abs(x - 100) < 0.5 && peak <= 100.01, "capsule: critically damped spring settles in 1 s without overshoot")
        let rows = [OptionRow(id: "a", label: "One"), OptionRow(id: "b", label: "Two"), OptionRow(id: "c", label: "Three")]
        let cards: [OrbCard] = [.chip("Sax · 12m"), .talk(tag: "Listening", text: "Go ahead"), .typing(tag: "Text only", reply: "Hi"),
                                .typing(tag: "Text only", reply: nil), .options(asked: "MrBeast's latest", rows: rows),
                                .followup(FollowCard(id: "f", about: "tasks", line: "Due today. Help?", yes: true)),
                                .followup(FollowCard(id: "g", about: "overheard", line: "What time?", ask: true)),
                                .countdown(line: "Sending hi to Mom."), .job(goal: "Fix deploy", step: "Testing", count: "3/5")]
        var inside = true, sides = true
        for c in cards {
            let g = CGRect(origin: .zero, size: CardLayout.glass(c))
            for side in [OrbSide.left, .right] {
                inside = inside && CardLayout.parts(c, side: side).allSatisfy { g.insetBy(dx: -0.5, dy: -0.5).contains($0.rect) }
                    && CardLayout.hits(c, side: side).allSatisfy { g.insetBy(dx: -0.5, dy: -0.5).contains($0.1) }
                let col = CardLayout.columnRect(c, side: side)
                sides = sides && (side == .right ? abs(col.maxX - g.maxX) < 0.01 : col.minX == 0)
                    && !CardLayout.parts(c, side: side).contains { $0.rect.intersects(col.insetBy(dx: 1, dy: 1)) && !c.isChip }
            }
        }
        check(inside && sides, "card: every part and tap target is inside the glass, her line at the screen-edge end, never under text")
        func hitSet(_ c: OrbCard) -> [Hit] { CardLayout.hits(c, side: .right).map(\.0) }
        check(hitSet(cards[4]) == [.row(0), .row(1), .row(2), .mark] && hitSet(cards[5]) == [.yes, .later, .no, .mark]
              && hitSet(cards[6]) == [.field, .no, .mark] && hitSet(cards[7]) == [.cancel, .mark]
              && hitSet(cards[8]) == [.stop, .mark] && hitSet(cards[2]) == [.field, .mark],
              "card: rows, Yes/Later/No, the type box, Cancel and Stop are each tappable")
        for side in [OrbSide.left, .right] {
            let c = CGPoint(x: side == .left ? 41 : 1399, y: 450)
            let m = OrbGeometry.markFrame(center: c)
            let f = OrbGeometry.cardFrame(markCenter: c, side: side, glass: CardLayout.glass(cards[4]), in: screen)
            let low = OrbGeometry.cardFrame(markCenter: CGPoint(x: c.x, y: 20), side: side, glass: CardLayout.glass(cards[4]), in: screen)
            check((side == .right ? abs(f.maxX - m.maxX) : abs(f.minX - m.minX)) < 0.01 && abs(f.midY - m.midY) < 0.01
                  && low.minY >= screen.minY,
                  "card: \(side.rawValue) side, it grows out of the capsule's edge and stays on screen")
        }
        var ci = OrbCard.Inputs()
        ci.said = "Playing it."; ci.linger = true
        let reply = OrbCard.pick(ci)
        ci.options = rows; ci.asked = "Which?"
        let opts = OrbCard.pick(ci)
        ci.typing = true
        let typing = OrbCard.pick(ci)
        var t = OrbCard.Inputs()
        t.said = "6.022e23 per mole."; t.linger = true; t.textMode = true; t.quietWhy = "Chem Class"
        let classReply = OrbCard.pick(t)
        var r = OrbCard.Inputs()
        r.nextEvent = "Sax · 12m"
        let rest = OrbCard.pick(r)
        r.followup = FollowCard(id: "f", line: "Due today"); r.followupFresh = true
        let fresh = OrbCard.pick(r)
        r.online = false
        check(reply == .talk(tag: "Evie", text: "Playing it.") && opts == .options(asked: "Which?", rows: rows)
              && typing == .typing(tag: "Type to Evie", reply: "Playing it.")
              && classReply == .typing(tag: "Chem Class · text only", reply: "6.022e23 per mole.")
              && rest == .chip("Sax · 12m") && fresh == .followup(FollowCard(id: "f", line: "Due today"))
              && OrbCard.pick(r) == .none && OrbCard.pick(OrbCard.Inputs()) == .none,
              "card: typing beats a list, a list beats a reply, class replies come with a type box, calm at rest")
        let now = Date(timeIntervalSince1970: 1_790_240_000)
        let evs = [CalEventDTO(title: "Sax Class", start: now.addingTimeInterval(12 * 60 - 5), end: now.addingTimeInterval(3600),
                               allDay: false, calendar: "Isaac"),
                   CalEventDTO(title: "Vedant Bday", start: now.addingTimeInterval(60), end: now.addingTimeInterval(86400),
                               allDay: true, calendar: "Isaac"),
                   CalEventDTO(title: "Chem Class", start: now.addingTimeInterval(3 * 3600), end: now.addingTimeInterval(4 * 3600),
                               allDay: false, calendar: "Isaac")]
        check(NextUp.chip(evs, now: now) == "Sax · 12m" && NextUp.chip(Array(evs.suffix(2)), now: now) == nil,
              "next up: a class within 30 min shows, all-day and far-off things don't")
        check(ActivityWatch.transition(idle: false, secondsSinceInput: 1300) == "idle"
              && ActivityWatch.transition(idle: false, secondsSinceInput: 100) == nil
              && ActivityWatch.transition(idle: true, secondsSinceInput: 3) == "back"
              && ActivityWatch.transition(idle: true, secondsSinceInput: 900) == nil,
              "activity: 20 min without input is idle, input again is back")
        check(MainActor.assumeIsolated { OrbStress.run(seconds: 3) }, "orb: 3 s of mouse moves, drags and state changes, no crash")
        let prog = try? CoreJSON.decoder.decode(CoreEvent.self, from: Data(
            #"{"kind":"job_progress","id":"j1","done":1,"total":4,"step":"Finding the missing env var"}"#.utf8))
        check(prog?.done == 1 && prog?.total == 4 && prog?.step == "Finding the missing env var", "decode job progress")
        // Spotify closed (2026-09-24 14:09 "Spotify isn't open"): launch, then wait until it answers AppleScript.
        final class Polls: @unchecked Sendable { var n = 0; var slept = 0.0; let sema = DispatchSemaphore(value: 0); var got: [Bool] = [] }
        let polls = Polls()
        Task.detached {
            let late = await SpotifyLaunch.waitReady(limit: 10, every: 0.25, ready: { polls.n += 1; return polls.n >= 3 },
                                                     sleep: { polls.slept += $0 })
            let pollsLate = polls.n
            polls.n = 0
            let never = await SpotifyLaunch.waitReady(limit: 2, every: 0.25, ready: { polls.n += 1; return false },
                                                      sleep: { polls.slept += $0 })
            polls.got = [late, pollsLate == 3, !never, polls.n == 9]
            polls.sema.signal()
        }
        _ = polls.sema.wait(timeout: .now() + 3)
        check(polls.got == [true, true, true, true], "spotify: waits for a just-launched Spotify, gives up after the limit")
        let box = StatusBox()
        Task.detached {
            box.result = await CoreClient(base: URL(string: "http://127.0.0.1:1")!).status()
            box.sema.signal()
        }
        let finished = box.sema.wait(timeout: .now() + 5) == .success
        check(finished && box.result == nil, "offline core returns nil, no crash")
        return ok
    }
}
