import Foundation

// Live events from the core's WebSocket (heard, verdict, say, state, job updates).
// Reconnects every 2s whenever the core restarts or the socket drops.
@MainActor
final class EventFeed {
    private let url = URL(string: "ws://127.0.0.1:8765/ws")!
    private let onEvent: (CoreEvent) -> Void

    init(onEvent: @escaping (CoreEvent) -> Void) {
        self.onEvent = onEvent
    }

    func run() async {
        while true {
            let task = URLSession.shared.webSocketTask(with: url)
            task.resume()
            while true {
                guard let msg = try? await task.receive() else { break }
                var data: Data?
                switch msg {
                case .string(let s): data = Data(s.utf8)
                case .data(let d): data = d
                @unknown default: data = nil
                }
                if let d = data, let ev = try? CoreJSON.decoder.decode(CoreEvent.self, from: d) {
                    onEvent(ev)
                }
            }
            task.cancel(with: .goingAway, reason: nil)
            try? await Task.sleep(for: .seconds(2))
        }
    }
}
