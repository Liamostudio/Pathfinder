import SwiftUI
import PhotosUI

struct ContentView: View {
    @StateObject private var model = CompanionModel()
    @State private var selectedPhoto: PhotosPickerItem?
    @Environment(\.scenePhase) private var scenePhase
    private let lime = Color(red: 0.835, green: 0.992, blue: 0.318)

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Text("See. Say. Act.").font(.largeTitle.bold())
                    Text("Meta Adventurer → iPhone → Pathfinder simulator").font(.caption).foregroundStyle(.secondary)
                    GroupBox("Laptop connection") {
                        VStack(spacing: 12) {
                            TextField("HTTPS gateway address", text: $model.address).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                            SecureField("Gateway pairing token", text: $model.pairingToken).textInputAutocapitalization(.never).autocorrectionDisabled()
                            Button("Connect to Pathfinder") { Task { await model.connect() } }.buttonStyle(.borderedProminent)
                                .disabled(model.busy || model.waitingForReview || model.microphone.recording)
                        }.textFieldStyle(.roundedBorder).padding(.vertical, 6)
                    }
                    MetaDeviceCard(connection: model.meta)
                    GroupBox("Spoken instruction") {
                        VStack(alignment: .leading, spacing: 14) {
                            if model.offline {
                                Text("Scripted mode: no speech recognition or paid API calls. Enter the demo transcript below.").font(.caption)
                            } else {
                                Toggle("Send my recording and optional photo to Meta", isOn: $model.consent)
                                Text("Muse Voice Transcribe reads the clip. You review its words before Spark can run the instruction. Recordings are not saved by Pathfinder.").font(.caption).foregroundStyle(.secondary)
                            }
                            MicrophoneCard(microphone: model.microphone, connected: model.connected, busy: model.busy || model.waitingForReview,
                                start: { Task { await model.startRecording() } }, finish: { model.finishRecording() })
                            TextField(model.offline ? "Demo transcript" : "Or type an instruction", text: $model.typedText, axis: .vertical)
                                .textFieldStyle(.roundedBorder).lineLimit(2...4)
                            HStack {
                                Button("Review typed instruction") { model.sendTyped() }.disabled(!model.connected || model.busy || model.waitingForReview || model.microphone.recording)
                                PhotosPicker(selection: $selectedPhoto, matching: .images) { Label("Attach glasses photo", systemImage: "photo") }
                                    .disabled(model.busy || model.waitingForReview || model.microphone.recording)
                            }.font(.caption)
                            if model.photo != nil {
                                HStack {
                                    Text("Photo attached from your library").font(.caption)
                                    Button("Remove") { model.photo = nil; selectedPhoto = nil }.disabled(model.busy || model.waitingForReview)
                                }
                            }
                            Text("Photo attachment uses an imported glasses photo; this version does not stream the glasses camera.").font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    if model.waitingForReview {
                        GroupBox("You said") {
                            VStack(alignment: .leading, spacing: 12) {
                                Text(model.transcript).font(.title3)
                                Button("Send to simulator") { Task { await model.confirm() } }.buttonStyle(.borderedProminent)
                                Button("Discard") { Task { await model.cancelReview() } }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    Text(model.status).font(.callout).accessibilityIdentifier("companion-status")
                    if !model.reply.isEmpty { Text(model.reply).font(.title3).padding().frame(maxWidth: .infinity, alignment: .leading).background(lime.opacity(0.25)).clipShape(RoundedRectangle(cornerRadius: 12)) }
                    Toggle("Speak model replies", isOn: $model.speakReplies)
                    Text("Select your glasses as the iPhone audio output to hear replies. Spoken STOP needs transcription; use the red STOP button for a direct stop request.").font(.caption).foregroundStyle(.secondary)
                }.padding()
            }
            .navigationTitle("Pathfinder").navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) {
                Button { Task { await model.stop() } } label: { Label("STOP SIMULATOR", systemImage: "stop.fill").bold().frame(maxWidth: .infinity).padding(10) }
                    .buttonStyle(.borderedProminent).tint(.red).padding().background(.ultraThinMaterial)
            }
            .onOpenURL { url in Task { await model.meta.handle(url) } }
            .onChange(of: scenePhase) { _, phase in if phase == .background { model.backgrounded() } }
            .onChange(of: selectedPhoto) { _, item in
                Task {
                    do { if let data = try await item?.loadTransferable(type: Data.self) { try model.attachPhoto(data) } }
                    catch { model.status = error.localizedDescription }
                }
            }
        }
    }
}

private struct MetaDeviceCard: View {
    @ObservedObject var connection: MetaConnection
    var body: some View {
        GroupBox("Meta glasses") {
            VStack(alignment: .leading, spacing: 10) {
                Text(connection.state).font(.caption)
                ForEach(connection.devices, id: \.self) { Text($0).font(.caption) }
                Button("Register with Meta AI") { Task { await connection.register() } }
                Text("Pair Adventurer in the Meta AI app and enable Developer Mode for SDK registration. Voice uses the iPhone Bluetooth audio route.").font(.caption2).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
private struct MicrophoneCard: View {
    @ObservedObject var microphone: GlassesMicrophone
    let connected: Bool
    let busy: Bool
    let start: () -> Void
    let finish: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button("Refresh Bluetooth microphones") { do { try microphone.refresh() } catch { microphone.route = error.localizedDescription } }.disabled(microphone.recording)
            Picker("Glasses microphone", selection: $microphone.selectedInput) {
                Text("Choose glasses").tag("")
                ForEach(microphone.inputs, id: \.uid) { Text($0.portName).tag($0.uid) }
            }.disabled(microphone.recording)
            Text(microphone.route).font(.caption)
            Button(microphone.recording ? "Finish recording" : "Tap to speak") { microphone.recording ? finish() : start() }
                .buttonStyle(.borderedProminent).disabled(!connected || busy)
            Text("Up to 30 seconds · glasses microphone required").font(.caption2).foregroundStyle(.secondary)
        }
    }
}
