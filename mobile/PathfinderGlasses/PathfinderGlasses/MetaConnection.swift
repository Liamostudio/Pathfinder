import Foundation
import Combine
import MWDATCore

@MainActor
final class MetaConnection: ObservableObject {
    @Published var state = "Not registered"
    @Published var devices: [String] = []
    private var registrationTask: Task<Void, Never>?
    private var devicesTask: Task<Void, Never>?

    init() {
        do { try Wearables.configure() }
        catch { state = "Meta SDK setup: \(error.localizedDescription)"; return }
        registrationTask = Task { [weak self] in
            for await value in Wearables.shared.registrationStateStream() {
                guard !Task.isCancelled else { return }
                self?.state = String(describing: value)
            }
        }
        devicesTask = Task { [weak self] in
            for await identifiers in Wearables.shared.devicesStream() {
                guard !Task.isCancelled else { return }
                self?.devices = identifiers.compactMap { id in
                    guard let device = Wearables.shared.deviceForIdentifier(id) else { return nil }
                    return "\(device.nameOrId()) · \(device.deviceType().rawValue)"
                }
            }
        }
    }
    func register() async {
        do { try await Wearables.shared.startRegistration() }
        catch { state = error.localizedDescription }
    }
    func handle(_ url: URL) async {
        do { _ = try await Wearables.shared.handleUrl(url) }
        catch { state = error.localizedDescription }
    }
    deinit { registrationTask?.cancel(); devicesTask?.cancel() }
}
