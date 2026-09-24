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

enum AudioCopy {
    // AVAudioEngine reuses a tap's buffer once the tap block returns. Handing that buffer to
    // another queue meant the converter sometimes read samples the engine was already
    // overwriting: garbled open-mic transcripts that the talk key never had (2026-09-23).
    static func copy(_ b: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let c = AVAudioPCMBuffer(pcmFormat: b.format, frameCapacity: b.frameLength) else { return nil }
        c.frameLength = b.frameLength
        let src = UnsafeMutableAudioBufferListPointer(b.mutableAudioBufferList)
        let dst = UnsafeMutableAudioBufferListPointer(c.mutableAudioBufferList)
        for (s, d) in zip(src, dst) {
            guard let sd = s.mData, let dd = d.mData else { continue }
            memcpy(dd, sd, Int(s.mDataByteSize))
        }
        for i in 0..<min(src.count, dst.count) { dst[i].mDataByteSize = src[i].mDataByteSize }
        return c
    }

    static func floats(_ data: Data, format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let n = data.count / MemoryLayout<Float>.size
        guard n > 0, let b = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(n)),
              let ch = b.floatChannelData else { return nil }
        b.frameLength = AVAudioFrameCount(n)
        data.withUnsafeBytes { raw in
            if let p = raw.baseAddress?.assumingMemoryBound(to: Float.self) { ch[0].update(from: p, count: n) }
        }
        return b
    }
}

// Open mic: streams the mic to the core as 16 kHz mono int16 over ws://127.0.0.1:8765/ws/ears,
// plus which app is in front (and whether that's a call app). The core does everything else:
// finding sentences, voice ID, Whisper. Runs only while the open mic isn't Off.
//
// Evie's voice plays through this same engine (/ws/mouth) with Apple's voice processing on, so
// the echo canceller knows exactly what she's saying and takes it out of the mic signal.
final class Ears: @unchecked Sendable {
    static let callApps: Set<String> = [
        "us.zoom.xos", "com.apple.FaceTime", "com.microsoft.teams2", "com.microsoft.teams",
        "com.cisco.webexmeetingsapp", "Cisco-Systems.Spark",
    ]

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let queue = DispatchQueue(label: "evie.ears")  // owns sockets, converter, packer, player
    private let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1,
                                          interleaved: true)!
    private var voiceFormat = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1)!
    private var converter: AVAudioConverter?
    private var packer = FramePacker()
    private var socket: URLSessionWebSocketTask?
    private var mouth: URLSessionWebSocketTask?
    private var line: String?  // the id of the line Evie is saying right now
    private var observer: NSObjectProtocol?
    private var keeper: Task<Void, Never>?
    private(set) var running = false
    private(set) var echoCancel = false
    var onVoiceLevel: ((Float) -> Void)?  // Evie's own loudness as she plays (the orb's speaking bars)

    @MainActor
    func start() -> Bool {
        guard !running else { return true }
        // Voice processing (echo cancellation + noise suppression + auto gain, like a call app)
        // is picky about the graph: try the layouts that work, best first, and log which one did.
        let layouts: [(vp: Bool, explicitOut: Bool, name: String)] = [
            (true, true, "echo cancel, mixer->output wired explicitly"),
            (true, false, "echo cancel, default wiring"),
            (false, false, "plain mic, no echo cancel"),
        ]
        for l in layouts {
            if tryStart(vp: l.vp, explicitOut: l.explicitOut) {
                NSLog("Evie ears: running (%@)", l.name)
                break
            }
            NSLog("Evie ears: layout failed (%@)", l.name)
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
            if engine.attachedNodes.contains(player) { engine.detach(player) }
            try? engine.inputNode.setVoiceProcessingEnabled(false)
            engine.reset()
        }
        guard running else { return false }
        observer = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.sendContext() }
        }
        keeper = Task { [weak self] in  // reconnect every 2s whenever the core restarts
            while !Task.isCancelled {
                self?.queue.async { self?.ensureSockets() }
                try? await Task.sleep(for: .seconds(2))
            }
        }
        return true
    }

    @MainActor
    private func tryStart(vp: Bool, explicitOut: Bool) -> Bool {
        let input = engine.inputNode
        echoCancel = false
        if vp {
            do {
                try input.setVoiceProcessingEnabled(true)
            } catch {
                NSLog("Evie ears: voice processing unavailable: %@", "\(error)")
                return false
            }
            if #available(macOS 14.0, *) {  // don't make Spotify quieter while the mic is on
                input.voiceProcessingOtherAudioDuckingConfiguration =
                    AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
            }
        }
        if explicitOut {
            let outFmt = engine.outputNode.inputFormat(forBus: 0)
            engine.connect(engine.mainMixerNode, to: engine.outputNode, format: outFmt.sampleRate > 0 ? outFmt : nil)
        }
        if vp {
            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: voiceFormat)
        }
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, let conv = AVAudioConverter(from: inFormat, to: outFormat) else {
            NSLog("Evie ears: no usable mic format %@", "\(inFormat)")
            return false
        }
        if inFormat.channelCount > 1 { conv.channelMap = [0] }  // voice processing can report extra channels
        queue.sync { converter = conv; packer = FramePacker() }
        input.installTap(onBus: 0, bufferSize: 2048, format: inFormat) { [weak self] buf, _ in
            guard let copy = AudioCopy.copy(buf) else { return }
            self?.queue.async { self?.process(copy) }
        }
        do {
            try engine.start()
        } catch {
            NSLog("Evie ears: engine wouldn't start: %@", "\(error)")
            return false
        }
        if vp { player.play() }
        echoCancel = vp
        running = true
        NSLog("Evie ears: mic format %@", "\(inFormat)")
        return true
    }

    @MainActor
    func stop() {
        guard running else { return }
        running = false
        keeper?.cancel()
        if let o = observer { NSWorkspace.shared.notificationCenter.removeObserver(o) }
        engine.inputNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        if engine.attachedNodes.contains(player) { engine.detach(player) }
        try? engine.inputNode.setVoiceProcessingEnabled(false)
        engine.reset()
        queue.async {
            self.socket?.cancel(with: .goingAway, reason: nil)
            self.socket = nil
            // Closing the mouth socket sends Evie's voice back to the core's own speaker output.
            self.mouth?.cancel(with: .goingAway, reason: nil)
            self.mouth = nil
            self.packer = FramePacker()
        }
    }

    private func ensureSockets() {
        if socket?.state != .running {
            socket?.cancel(with: .goingAway, reason: nil)
            let s = URLSession.shared.webSocketTask(with: URL(string: "ws://127.0.0.1:8765/ws/ears")!)
            s.resume()
            socket = s
            DispatchQueue.main.async { MainActor.assumeIsolated { self.sendContext() } }
        }
        // Only take over Evie's voice when echo cancellation is actually on.
        if echoCancel, mouth?.state != .running {
            mouth?.cancel(with: .goingAway, reason: nil)
            let m = URLSession.shared.webSocketTask(with: URL(string: "ws://127.0.0.1:8765/ws/mouth")!)
            m.maximumMessageSize = 4 * 1024 * 1024
            m.resume()
            mouth = m
            m.send(.string(#"{"kind":"hello","echo_cancel":true}"#)) { _ in }
            listen(m)
        }
    }

    // MARK: Evie's voice

    private func listen(_ m: URLSessionWebSocketTask) {
        m.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                guard case .success(let msg) = result, self.mouth === m else { return }
                switch msg {
                case .data(let d): self.schedule(d)
                case .string(let s): self.control(s)
                @unknown default: break
                }
                self.listen(m)
            }
        }
    }

    private func control(_ text: String) {
        guard let obj = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
              let kind = obj["kind"] as? String, let id = obj["id"] as? String else { return }
        switch kind {
        case "start":
            if MouthGate.onStart(playing: line, new: id) == .flushOldThenPlay, let old = line {
                // Never two voices at once: a new line while one is still audible flushes it.
                SpeechLog.write(["ev": "OVERLAP", "old": old, "new": id])
                player.stop()
                done(old)
            }
            SpeechLog.write(["ev": "start", "id": id])
            let rate = (obj["rate"] as? Double) ?? 24000
            if rate != voiceFormat.sampleRate, let f = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1) {
                voiceFormat = f
                engine.connect(player, to: engine.mainMixerNode, format: f)
            }
            line = id
            if !player.isPlaying { player.play() }
        case "end":
            // A 1-frame buffer after the last real one: its completion means everything played.
            guard let tail = AVAudioPCMBuffer(pcmFormat: voiceFormat, frameCapacity: 1) else { done(id); return }
            tail.frameLength = 1
            player.scheduleBuffer(tail, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                self?.queue.async { self?.done(id) }
            }
        case "stop":
            player.stop()  // drops everything queued, within a buffer
            player.play()
            done(id)
        default:
            break
        }
    }

    private func schedule(_ data: Data) {
        guard line != nil, let buf = AudioCopy.floats(data, format: voiceFormat) else { return }
        player.scheduleBuffer(buf, completionHandler: nil)
        if let cb = onVoiceLevel, let ch = buf.floatChannelData, buf.frameLength > 0 {
            var sum: Float = 0
            for i in 0..<Int(buf.frameLength) { sum += ch[0][i] * ch[0][i] }
            cb(min(1, (sum / Float(buf.frameLength)).squareRoot() * 5))
        }
    }

    private func done(_ id: String) {
        SpeechLog.write(["ev": "done", "id": id])
        if line == id { line = nil }
        mouth?.send(.string(#"{"kind":"done","id":"\#(id)"}"#)) { _ in }
    }

    // MARK: Mic

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


// One voice at a time, as a pure rule the selftest can check.
enum MouthGate {
    enum Start: Equatable { case play, flushOldThenPlay }

    static func onStart(playing: String?, new: String) -> Start {
        guard let p = playing, p != new else { return .play }
        return .flushOldThenPlay
    }
}

// ~/Library/Logs/Evie/speech.jsonl, shared with the core: when each line really started and
// finished playing. It's how an overlap gets caught in the act.
enum SpeechLog {
    private static let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/Evie/speech.jsonl")
    private static let q = DispatchQueue(label: "evie.speechlog")

    static func write(_ row: [String: String]) {
        let t = Date().timeIntervalSince1970
        q.async {
            var r: [String: Any] = row
            r["src"] = "app"
            r["t"] = t
            guard var d = try? JSONSerialization.data(withJSONObject: r) else { return }
            d.append(0x0A)
            if let h = try? FileHandle(forWritingTo: url) {
                h.seekToEndOfFile()
                h.write(d)
                try? h.close()
            } else {
                try? d.write(to: url)
            }
        }
    }
}
