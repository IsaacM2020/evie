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
        var req = URLRequest(url: base.appendingPathComponent("decide"))
        req.httpMethod = "POST"
        req.timeoutInterval = 8
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            req.httpBody = try JSONSerialization.data(withJSONObject: [
                "utterance": utterance, "speaker": speaker, "in_call": inCall,
            ])
            let (data, _) = try await URLSession.shared.data(for: req)
            return .success(try CoreJSON.decoder.decode(OutcomeDTO.self, from: data))
        } catch {
            return .failure(error)
        }
    }
}
