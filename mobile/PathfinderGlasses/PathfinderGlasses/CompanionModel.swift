import AVFoundation
import Foundation
import Combine
import UIKit

@MainActor
final class CompanionModel: ObservableObject {
    @Published var address = UserDefaults.standard.string(forKey: "gatewayAddress") ?? "https://pathfinder.local:8772"
    @Published var pairingToken = PairingStore.read()
    @Published var status = "Connect to the laptop gateway."
    @Published var connected = false
    @Published var consent = false
    @Published var offline = false
    @Published var typedText = ""
    @Published var transcript = ""
    @Published var reply = ""
    @Published var busy = false
    @Published var waitingForReview = false
    @Published var photo: Data?
    @Published var speakReplies = true
    let microphone = GlassesMicrophone()
    let meta = MetaConnection()
    private let speaker = AVSpeechSynthesizer()
    private var client: GatewayClient?
    private var jobID: String?
    private var requestTask: Task<Void, Never>?
    private var generation = 0
    private var microphoneUpdates: AnyCancellable?

    init() {
        microphoneUpdates = microphone.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
        microphone.routeFailure = { [weak self] error in self?.status = error }
        microphone.timedStop = { [weak self] in self?.finishRecording() }
    }
    func connect() async {
        guard !busy, !waitingForReview, !microphone.recording else { return }
        generation += 1
        let attempt = generation
        do {
            let next = try GatewayClient(address: address.trimmingCharacters(in: .whitespacesAndNewlines), token: pairingToken)
            let info = try await next.status()
            guard generation == attempt else { return }
            guard info.available, info.simulator_only else { throw GatewayError(message: "This companion requires the simulator gateway.") }
            client = next; offline = info.voice_mode == "scripted"; connected = true
            status = "Connected · \(info.model) · simulator"
            UserDefaults.standard.set(address, forKey: "gatewayAddress")
            try PairingStore.save(pairingToken)
        } catch { if generation == attempt { connected = false; status = error.localizedDescription } }
    }
    func startRecording() async {
        guard connected, !busy, !waitingForReview else { return }
        guard offline || consent else { status = "Allow sending the recording to Meta before speaking."; return }
        speaker.stopSpeaking(at: .immediate)
        let attempt = generation; busy = true
        do {
            try await microphone.start()
            guard generation == attempt else { return }
            busy = false; status = "Speak, then tap Finish recording."
        } catch { if generation == attempt { microphone.cancel(); busy = false; status = error.localizedDescription } }
    }
    func finishRecording() {
        do { send(audio: try microphone.finish()) }
        catch { status = error.localizedDescription }
    }
    func sendTyped() { send(audio: nil) }
    private func send(audio: Data?) {
        guard let client, !busy, !waitingForReview, !microphone.recording else { return }
        if !offline && (audio != nil || photo != nil) && !consent { status = "Allow sending this recording/photo to Meta first."; return }
        let text = typedText.trimmingCharacters(in: .whitespacesAndNewlines)
        if audio == nil && text.isEmpty { status = "Speak or type an instruction."; return }
        var body: [String: Any] = ["request_id": UUID().uuidString, "send_to_meta": consent]
        if let audio { body["audio_wav"] = audio.base64EncodedString(); if offline { body["demo_transcript"] = text } }
        else { body["text"] = text }
        if let photo { body["image_jpeg"] = photo.base64EncodedString() }
        busy = true; waitingForReview = false; transcript = ""; reply = ""
        generation += 1; let attempt = generation
        requestTask = Task {
            do {
                let job: InputJob = try await client.call("v1/input", body: body)
                guard generation == attempt else { return }
                if job.status == "stopped" { busy = false; status = "Stopped"; return }
                jobID = job.identifier
                try await poll(client: client, id: job.identifier, generation: attempt)
            } catch { if generation == attempt { busy = false; status = error.localizedDescription } }
        }
    }
    private func poll(client: GatewayClient, id: String, generation attempt: Int) async throws {
        for _ in 0..<1200 {
            try Task.checkCancellation()
            let job = try await client.job(id)
            guard generation == attempt else { return }
            switch job.status {
            case "awaiting_confirmation":
                transcript = job.transcript ?? ""; waitingForReview = true; busy = false
                status = "Review the words, then send to the simulator."
                speak("I heard: \(transcript). Review on your iPhone."); return
            case "completed":
                reply = job.result?.reply ?? "Command completed."; status = "Completed"; busy = false; jobID = nil; photo = nil
                speak(reply); return
            case "stopped", "cancelled", "expired", "error":
                busy = false; waitingForReview = false; jobID = nil
                status = job.error ?? job.result?.error ?? job.status.capitalized; return
            default: status = job.status == "transcribing" ? "Meta is transcribing…" : "Simulator is working…"
            }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        throw GatewayError(message: "The request is still pending. Use STOP or check the laptop.")
    }
    func confirm() async {
        guard let client, let id = jobID, waitingForReview, !busy else { return }
        guard offline || photo == nil || consent else { status = "Allow sending this photo to Meta, or discard this input."; return }
        waitingForReview = false; busy = true
        let attempt = generation
        requestTask = Task {
            do {
                let _: InputJob = try await client.call("v1/confirm", body: ["job_id": id])
                guard generation == attempt else { return }
                try await poll(client: client, id: id, generation: attempt)
            } catch { if generation == attempt { busy = false; status = error.localizedDescription } }
        }
    }
    func cancelReview() async {
        guard let client, let id = jobID else { return }
        let attempt = generation; busy = true
        do {
            let _: InputJob = try await client.call("v1/cancel", body: ["job_id": id])
            guard generation == attempt else { return }
            waitingForReview = false; busy = false; jobID = nil; photo = nil; status = "Discarded"
        } catch { if generation == attempt { busy = false; status = error.localizedDescription } }
    }
    func stop() async {
        generation += 1
        let attempt = generation
        requestTask?.cancel(); microphone.cancel(); speaker.stopSpeaking(at: .immediate)
        busy = false; waitingForReview = false; jobID = nil
        guard let client else { status = "Not connected. Use the laptop STOP control."; return }
        do { _ = try await client.stop(); if generation == attempt { status = "STOP confirmed. Reset at the laptop to continue." } }
        catch { if generation == attempt { status = "STOP could not be confirmed. Use the laptop STOP control." } }
    }
    func backgrounded() {
        // Cancel a pending microphone start as well as an active recording.
        if microphone.recording || (busy && jobID == nil) { generation += 1; busy = false }
        microphone.cancel(); speaker.stopSpeaking(at: .immediate)
        if waitingForReview { Task { await cancelReview() } }
    }
    func attachPhoto(_ data: Data) throws {
        guard !busy, !waitingForReview, !microphone.recording else { return }
        guard let image = UIImage(data: data) else { throw GatewayError(message: "Choose a glasses photo.") }
        let factor = min(1, 1024 / max(image.size.width, image.size.height))
        let size = CGSize(width: image.size.width * factor, height: image.size.height * factor)
        let resized = UIGraphicsImageRenderer(size: size).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        guard let jpeg = resized.jpegData(compressionQuality: 0.65), jpeg.count <= 400_000 else { throw GatewayError(message: "Choose a smaller photo.") }
        photo = jpeg
    }
    private func speak(_ text: String) {
        guard speakReplies, !text.isEmpty else { return }
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
        try? AVAudioSession.sharedInstance().setActive(true)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-GB")
        speaker.speak(utterance)
    }
}
