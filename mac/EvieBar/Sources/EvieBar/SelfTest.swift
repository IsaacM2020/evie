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
        func ptt(_ inputs: [PTTInput]) -> [PTTAction] {
            var p = PushToTalk()
            return inputs.map { p.handle($0) }
        }
        check(ptt([.fnDown(at: 0), .fnUp(at: 0.1)]) == [.startRecording, .cancel], "ptt: quick tap cancels")
        check(ptt([.fnDown(at: 0), .fnUp(at: 1.2)]) == [.startRecording, .stopAndSend], "ptt: hold sends")
        check(ptt([.fnDown(at: 0), .otherKey, .fnUp(at: 1)]) == [.startRecording, .cancel, .none],
              "ptt: Fn+arrow cancels, release does nothing")
        check(ptt([.fnDown(at: 0), .fnDown(at: 0.5), .fnUp(at: 1)]) == [.startRecording, .none, .stopAndSend],
              "ptt: repeated down ignored")
        check(ptt([.fnUp(at: 1), .otherKey]) == [.none, .none], "ptt: up/other without down do nothing")
        check(ptt([.fnDown(at: 0), .fnUp(at: 0.25)]) == [.startRecording, .stopAndSend], "ptt: exactly 0.25s sends")
        let jobEv = try? CoreJSON.decoder.decode(CoreEvent.self, from: Data(
            #"{"kind":"job_event","t":1.5,"id":"ab12","line":"Ran: pytest","extra":[1,2]}"#.utf8))
        check(jobEv?.kind == "job_event" && jobEv?.line == "Ran: pytest" && jobEv?.id == "ab12", "decode job_event")
        let st = try? CoreJSON.decoder.decode(CoreStatus.self, from: Data(
            #"{"ok":true,"version":"0.1.0","jev_ok":true,"stt_ready":true,"voice_ready":true,"calendar_fresh":false,"job":null}"#.utf8))
        check(st?.calendarFresh == false && st?.sttReady == true, "decode phase 1 status")
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
