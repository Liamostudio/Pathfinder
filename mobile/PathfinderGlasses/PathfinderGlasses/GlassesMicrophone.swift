import AVFoundation
import Foundation
import Combine

@MainActor
final class GlassesMicrophone: NSObject, ObservableObject {
    @Published var inputs: [AVAudioSessionPortDescription] = []
    @Published var selectedInput = ""
    @Published var recording = false
    @Published var route = "No glasses microphone selected"
    var routeFailure: ((String) -> Void)?
    private var recorder: AVAudioRecorder?
    private var file: URL?
    private var timer: Timer?
    private var startID: UUID?
    private let audioDirectory = FileManager.default.temporaryDirectory.appendingPathComponent("PathfinderGlassesAudio", isDirectory: true)
    private var routeObserver: NSObjectProtocol?
    private var interruptionObserver: NSObjectProtocol?
    var timedStop: (() -> Void)?

    override init() {
        super.init()
        // Remove clips left by an interrupted process; never scan another app's files.
        for item in (try? FileManager.default.contentsOfDirectory(at: audioDirectory, includingPropertiesForKeys: nil)) ?? [] {
            if item.pathExtension == "wav" { try? FileManager.default.removeItem(at: item) }
        }
        routeObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification,
            object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.checkRoute() }
        }
        interruptionObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification,
            object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.recording else { return }
                self.cancel(); self.routeFailure?("Recording cancelled by an audio interruption. Record again.")
            }
        }
    }
    func refresh() throws {
        let audio = AVAudioSession.sharedInstance()
        try audio.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth])
        try audio.setActive(true)
        inputs = (audio.availableInputs ?? []).filter { $0.portType == .bluetoothHFP }
        if !inputs.contains(where: { $0.uid == selectedInput }) { selectedInput = inputs.count == 1 ? inputs[0].uid : "" }
        route = inputs.isEmpty ? "Pair Adventurer with this iPhone, then refresh." : "Choose your glasses microphone below."
    }
    func start() async throws {
        guard !recording, startID == nil else { throw GatewayError(message: "Microphone is already starting.") }
        let attempt = UUID(); startID = attempt
        defer { if startID == attempt { startID = nil } }
        let permitted = await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { continuation.resume(returning: $0) }
        }
        guard startID == attempt else { throw CancellationError() }
        try Task.checkCancellation()
        guard permitted else { throw GatewayError(message: "Allow microphone access in iPhone Settings.") }
        try refresh()
        guard let input = inputs.first(where: { $0.uid == selectedInput }) else {
            throw GatewayError(message: "Select the Adventurer Bluetooth microphone. The phone microphone is not used as a fallback.")
        }
        let audio = AVAudioSession.sharedInstance()
        try audio.setPreferredInput(input)
        for _ in 0..<40 {
            guard startID == attempt else { throw CancellationError() }
            try Task.checkCancellation()
            if audio.currentRoute.inputs.contains(where: { $0.uid == input.uid && $0.portType == .bluetoothHFP }) { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        guard startID == attempt else { throw CancellationError() }
        guard audio.currentRoute.inputs.contains(where: { $0.uid == input.uid && $0.portType == .bluetoothHFP }) else {
            throw GatewayError(message: "The glasses microphone did not become active. Check Bluetooth and retry.")
        }
        try FileManager.default.createDirectory(at: audioDirectory, withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete])
        let url = audioDirectory.appendingPathComponent(UUID().uuidString + ".wav")
        file = url
        let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16, AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsFloatKey: false]
        do {
            recorder = try AVAudioRecorder(url: url, settings: settings)
            guard recorder?.record(forDuration: 30) == true else { throw GatewayError(message: "Could not start the glasses microphone.") }
            recording = true; route = "Recording: \(input.portName)"
            timer = Timer.scheduledTimer(withTimeInterval: 29.5, repeats: false) { [weak self] _ in
                Task { @MainActor in self?.timedStop?() }
            }
        } catch { cancel(); throw error }
    }
    private func checkRoute() {
        guard recording else { return }
        if !AVAudioSession.sharedInstance().currentRoute.inputs.contains(where: { $0.uid == selectedInput && $0.portType == .bluetoothHFP }) {
            cancel(); routeFailure?("Glasses disconnected. The clip was discarded; reconnect and record again.")
        }
    }
    func finish() throws -> Data {
        guard recording, let url = file else { throw GatewayError(message: "There is no recording to send.") }
        checkRoute()
        guard recording else { throw GatewayError(message: "Audio route changed; the clip was discarded.") }
        recorder?.stop(); recording = false; timer?.invalidate(); timer = nil
        defer { cleanup() }
        let data = try Data(contentsOf: url)
        guard data.count > 44, data.count <= 1_440_044 else { throw GatewayError(message: "Record a short instruction, up to 30 seconds.") }
        return data
    }
    func cancel() { startID = nil; recorder?.stop(); recording = false; timer?.invalidate(); timer = nil; cleanup() }
    private func cleanup() {
        recorder = nil
        if let file { try? FileManager.default.removeItem(at: file) }; file = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
    deinit {
        if let routeObserver { NotificationCenter.default.removeObserver(routeObserver) }
        if let interruptionObserver { NotificationCenter.default.removeObserver(interruptionObserver) }
    }
}
