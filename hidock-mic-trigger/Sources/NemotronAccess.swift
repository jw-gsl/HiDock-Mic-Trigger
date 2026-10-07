import Foundation

/// How the app reaches the Nemotron 3 Diarization service on an NVIDIA host
/// (an NVIDIA DGX Spark — "ember" — on the tailnet).
///
/// The service rejects requests without its `X-Auth-Token`. The token lives
/// in the Keychain, entered on the Models page, and reaches the Python
/// pipeline only through the subprocess environment — never a file — exactly
/// like the Hugging Face token. The endpoint is a plain setting so the host
/// isn't hardcoded.
enum NemotronAccess {
    static let token = KeychainSecret(
        service: "com.hidock.tools.nemotron",
        account: "sidecar-auth-token",
        label: "HiDock NVIDIA Spark diarization token")

    static let endpointKey = "nemotronEndpoint"
    static let defaultEndpoint = "http://ember.tail17bf47.ts.net:8890"

    /// Base URL of the service (no `/diarize`).
    static var endpoint: String {
        let saved = UserDefaults.standard.string(forKey: endpointKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return saved.isEmpty ? defaultEndpoint : saved
    }

    /// Hand the endpoint and token to a Python subprocess.
    /// `shared/diarize_nemotron.py` reads these before config.toml.
    static func inject(into environment: inout [String: String]) {
        environment["HIDOCK_NEMOTRON_ENDPOINT"] = endpoint
        if let value = token.load() {
            environment["HIDOCK_NEMOTRON_TOKEN"] = value
        }
    }
}
