import AVFoundation

// Records the mic to a 16 kHz mono 16-bit WAV (what Whisper wants) while Fn is held.
@MainActor
final class Recorder {
    private var recorder: AVAudioRecorder?
    private let url = FileManager.default.temporaryDirectory.appendingPathComponent("evie-ptt.wav")

    static func requestMic() async -> Bool {
        await AVCaptureDevice.requestAccess(for: .audio)
    }

    func start() -> Bool {
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        recorder = try? AVAudioRecorder(url: url, settings: settings)
        recorder?.isMeteringEnabled = true  // the orb's listening bars follow Isaac's voice
        return recorder?.record() ?? false
    }

    /// 0...1 loudness of the mic right now (for the orb).
    func level() -> Float {
        guard let r = recorder else { return 0 }
        r.updateMeters()
        return max(0, min(1, (r.averagePower(forChannel: 0) + 50) / 50))
    }

    func stop() -> Data? {
        guard let r = recorder else { return nil }
        r.stop()
        recorder = nil
        return try? Data(contentsOf: url)
    }

    func cancel() {
        recorder?.stop()
        recorder?.deleteRecording()
        recorder = nil
    }
}
