import AppKit
import AVFoundation

// Cuts converted mic audio into the 512-sample frames the core's VAD wants.
// Pure (no audio APIs) so the selftest can check it.
struct FramePacker {
    static let frame = 512
    private(set) var pending: [Int16] = []

    mutating func add(_ samples: [Int16]) -> [Data] {
        pending.append(contentsOf: samples)
        var out: [Data] = []
        while pending.count >= Self.frame {
            let chunk = Array(pending[0..<Self.frame])
            pending.removeFirst(Self.frame)
            out.append(chunk.withUnsafeBufferPointer { Data(buffer: $0) })  // little-endian on Apple silicon
        }
        return out
    }
}

// Open mic: streams the mic to the core as 16 kHz mono int16 over ws://127.0.0.1:8765/ws/ears,
// plus which app is in front (and whether that's a call app). The core does everything else:
// finding sentences, voice ID, Whisper. Runs only while the open mic isn't Off.
final class Ears: @unchecked Sendable {
    static let callApps: Set<String> = [
        "us.zoom.xos", "com.apple.FaceTime", "com.microsoft.teams2", "com.microsoft.teams",
        "com.cisco.webexmeetingsapp", "Cisco-Systems.Spark",
    ]

    private let engine = AVAudioEngine()
    private let queue = DispatchQueue(label: "evie.ears")  // owns socket, converter, packer
    private let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1,
                                          interleaved: true)!
    private var converter: AVAudioConverter?
    private var packer = FramePacker()
    private var socket: URLSessionWebSocketTask?
    private var observer: NSObjectProtocol?
    private var keeper: Task<Void, Never>?
    private(set) var running = false

    @MainActor
    func start() -> Bool {
        guard !running else { return true }
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, let conv = AVAudioConverter(from: inFormat, to: outFormat) else { return false }
        queue.sync { converter = conv; packer = FramePacker() }
        input.installTap(onBus: 0, bufferSize: 2048, format: inFormat) { [weak self] buf, _ in
            self?.queue.async { self?.process(buf) }
        }
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            return false
        }
        running = true
        observer = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.sendContext() }
        }
        keeper = Task { [weak self] in  // reconnect every 2s whenever the core restarts
            while !Task.isCancelled {
                self?.queue.async { self?.ensureSocket() }
                try? await Task.sleep(for: .seconds(2))
            }
        }
        return true
    }

    @MainActor
    func stop() {
        guard running else { return }
        running = false
        keeper?.cancel()
        if let o = observer { NSWorkspace.shared.notificationCenter.removeObserver(o) }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        queue.async {
            self.socket?.cancel(with: .goingAway, reason: nil)
            self.socket = nil
            self.packer = FramePacker()
        }
    }

    private func ensureSocket() {
        if let s = socket, s.state == .running { return }
        socket?.cancel(with: .goingAway, reason: nil)
        let s = URLSession.shared.webSocketTask(with: URL(string: "ws://127.0.0.1:8765/ws/ears")!)
        s.resume()
        socket = s
        DispatchQueue.main.async { MainActor.assumeIsolated { self.sendContext() } }
    }

    private func process(_ buf: AVAudioPCMBuffer) {
        guard let conv = converter, let s = socket, s.state == .running else { return }
        let cap = AVAudioFrameCount(Double(buf.frameLength) * 16000 / buf.format.sampleRate) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        conv.convert(to: out, error: &err) { _, status in
            if fed {
                status.pointee = .noDataNow  // keeps the resampler's state for the next buffer
                return nil
            }
            fed = true
            status.pointee = .haveData
            return buf
        }
        guard err == nil, let ch = out.int16ChannelData, out.frameLength > 0 else { return }
        let samples = Array(UnsafeBufferPointer(start: ch[0], count: Int(out.frameLength)))
        for frame in packer.add(samples) {
            s.send(.data(frame)) { _ in }
        }
    }

    @MainActor
    func sendContext() {
        let app = NSWorkspace.shared.frontmostApplication
        let payload: [String: Any] = [
            "front_app": app?.localizedName ?? "",
            "in_call": Ears.callApps.contains(app?.bundleIdentifier ?? ""),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else { return }
        queue.async { self.socket?.send(.string(text)) { _ in } }
    }
}
