import AppKit
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
        let screen = NSRect(x: 0, y: 0, width: 1440, height: 900)
        let size = NSSize(width: 200, height: 60)
        check(PillPlacement.clamp(NSPoint(x: 1400, y: -50), size: size, in: screen) == NSPoint(x: 1240, y: 0)
              && PillPlacement.clamp(NSPoint(x: 100, y: 100), size: size, in: screen) == NSPoint(x: 100, y: 100),
              "pill: stays fully on screen")
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
