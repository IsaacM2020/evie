import Foundation

struct CoreClient {
    var base: URL = URL(string: "http://127.0.0.1:8765")!

    func status() async -> CoreStatus? {
        var req = URLRequest(url: base.appendingPathComponent("status"))
        req.timeoutInterval = 1.5
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return try? CoreJSON.decoder.decode(CoreStatus.self, from: data)
    }

    func decide(utterance: String, speaker: String, inCall: Bool) async -> Result<OutcomeDTO, Error> {
        do {
            let body = try JSONSerialization.data(withJSONObject: [
                "utterance": utterance, "speaker": speaker, "in_call": inCall,
            ])
            let data = try await post("decide", body: body, type: "application/json", timeout: 8)
            return .success(try CoreJSON.decoder.decode(OutcomeDTO.self, from: data))
        } catch {
            return .failure(error)
        }
    }

    func hear(_ text: String, speaker: String) async -> TurnDTO? {
        guard let body = try? JSONSerialization.data(withJSONObject: ["text": text, "speaker": speaker]),
              let data = try? await post("hear", body: body, type: "application/json", timeout: 20)
        else { return nil }
        return try? CoreJSON.decoder.decode(TurnDTO.self, from: data)
    }

    func voiceStart() async {
        _ = try? await post("voice/start", body: Data(), type: "application/json", timeout: 2)
    }

    /// channel "live": recorded through the echo-cancelled open-mic engine (teaches the live voiceprint).
    func voice(_ wav: Data, channel: String = "raw") async -> TurnDTO? {
        guard let data = try? await post("voice", body: wav, type: "audio/wav", timeout: 30,
                                         headers: ["X-Evie-Channel": channel]) else { return nil }
        return try? CoreJSON.decoder.decode(TurnDTO.self, from: data)
    }

    func settings(showWork: Bool) async {
        let body = (try? JSONSerialization.data(withJSONObject: ["show_work": showWork])) ?? Data()
        _ = try? await post("settings", body: body, type: "application/json", timeout: 3)
    }

    func stopAll() async {
        _ = try? await post("stop", body: Data(), type: "application/json", timeout: 5)
    }

    func stopJob() async {
        _ = try? await post("job/stop", body: Data(), type: "application/json", timeout: 10)
    }

    /// Switch the open mic. Returns the new state, or the core's reason for refusing (e.g. Live
    /// before Evie knows Isaac's voice).
    func setEarsMode(_ mode: String) async -> Result<EarsDTO, CoreRefusal> {
        await earsCall("ears/mode", ["mode": mode])
    }

    func enroll(_ on: Bool) async -> Result<EarsDTO, CoreRefusal> {
        await earsCall("voiceid/enroll", ["on": on])
    }

    private func earsCall(_ path: String, _ json: [String: Any]) async -> Result<EarsDTO, CoreRefusal> {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.timeoutInterval = 5
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: json)
        guard let (data, resp) = try? await URLSession.shared.data(for: req) else {
            return .failure(CoreRefusal(detail: "Can't reach Evie's core.", status: 0))
        }
        if (resp as? HTTPURLResponse)?.statusCode == 200, let e = try? CoreJSON.decoder.decode(EarsDTO.self, from: data) {
            return .success(e)
        }
        let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
        return .failure(CoreRefusal(detail: detail ?? "Evie's core said no.",
                                    status: (resp as? HTTPURLResponse)?.statusCode ?? 0))
    }

    func handsResult(id: String, _ o: HandsOutcome) async {
        let body: [String: Any] = ["id": id, "ok": o.ok, "detail": o.detail, "data": o.data]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return }
        _ = try? await post("hands/result", body: data, type: "application/json", timeout: 5)
    }

    /// The debug recorder (keeps Isaac's open-mic sentences for 7 days so the ears can be tuned).
    func recorder(set on: Bool? = nil) async -> Bool? {
        var req = URLRequest(url: base.appendingPathComponent("recorder"))
        req.timeoutInterval = 3
        if let on {
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: ["on": on])
        }
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj["on"] as? Bool
    }

    func postCalendar(_ body: Data) async -> Bool {
        (try? await post("calendar", body: body, type: "application/json", timeout: 5)) != nil
    }

    private func post(_ path: String, body: Data, type: String, timeout: TimeInterval,
                      headers: [String: String] = [:]) async throws -> Data {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.timeoutInterval = timeout
        req.setValue(type, forHTTPHeaderField: "Content-Type")
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        req.httpBody = body
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return data
    }
}

struct CoreRefusal: Error, Equatable {
    let detail: String
    var status = 0  // 0 = couldn't reach the core at all
}

// Switching the Live mic on right after a restart (2026-09-24): the core answers 503 while it
// warms up, and the app used to blame Isaac's voice. Now only a real 409 means "train me".
enum LiveSwitch {
    static func shouldRetry(status: Int) -> Bool { status == 0 || status == 503 }

    static func note(status: Int, detail: String, clipsLeft: Int) -> String {
        if status == 409 { return "Hold ⌃⌥ and talk \(clipsLeft) more times so I learn your voice" }
        if shouldRetry(status: status) { return "Evie's still starting up, try again in a sec" }
        return detail
    }
}
