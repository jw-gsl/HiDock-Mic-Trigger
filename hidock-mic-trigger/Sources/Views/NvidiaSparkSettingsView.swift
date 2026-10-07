import SwiftUI

/// Connection settings for the NVIDIA DGX Spark that runs Nemotron 3
/// Diarization: where it is, and the token it requires. Shown on the Nemotron
/// row once "I have an NVIDIA DGX Spark" is on. The token is held in the
/// Keychain (same handling as the Hugging Face token) and never displayed.
struct NvidiaSparkSettingsView: View {
    /// Re-run the connection check after a change.
    let onCheck: () -> Void

    @State private var endpointEntry = NemotronAccess.endpoint
    @State private var tokenEntry = ""
    @State private var redacted = NemotronAccess.token.redacted()
    @State private var message: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text("Host").font(.caption).frame(width: 40, alignment: .leading)
                TextField(NemotronAccess.defaultEndpoint, text: $endpointEntry)
                    .textFieldStyle(.roundedBorder)
                    .font(.caption.monospaced())
                    .frame(maxWidth: 300)
                    .onSubmit(saveEndpoint)
                if endpointEntry.trimmingCharacters(in: .whitespaces) != NemotronAccess.endpoint {
                    Button("Save", action: saveEndpoint).controlSize(.small)
                }
            }
            HStack(spacing: 6) {
                Text("Token").font(.caption).frame(width: 40, alignment: .leading)
                if let shown = redacted {
                    HStack(spacing: 6) {
                        Image(systemName: "lock.fill").font(.caption).foregroundColor(.secondary)
                        Text(shown).font(.caption.monospaced()).foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .frame(maxWidth: 300, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 5).fill(Color.secondary.opacity(0.08)))
                    .help("Stored in your Keychain. Remove it to enter a different token.")
                    Button("Remove") {
                        NemotronAccess.token.delete()
                        redacted = nil
                        message = "Token removed."
                    }
                    .controlSize(.small)
                } else {
                    // SecureField: never rendered, screenshotted or recorded.
                    SecureField("paste the service's X-Auth-Token", text: $tokenEntry)
                        .textFieldStyle(.roundedBorder)
                        .font(.caption.monospaced())
                        .frame(maxWidth: 300)
                        .onSubmit(saveToken)
                    Button("Save", action: saveToken)
                        .controlSize(.small)
                        .disabled(tokenEntry.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            Text(message ?? "The token is the contents of ~/.nemo-diar-token on the Spark. It is kept in your Keychain and passed to the pipeline in memory only.")
                .font(.caption2)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 2)
    }

    private func saveEndpoint() {
        var value = endpointEntry.trimmingCharacters(in: .whitespacesAndNewlines)
        while value.hasSuffix("/") { value.removeLast() }
        if value.hasSuffix("/diarize") { value.removeLast("/diarize".count) }
        guard value.isEmpty || value.hasPrefix("http://") || value.hasPrefix("https://") else {
            message = "The host must start with http:// or https://"
            return
        }
        UserDefaults.standard.set(value, forKey: NemotronAccess.endpointKey)
        endpointEntry = NemotronAccess.endpoint
        message = "Host saved."
        onCheck()
    }

    private func saveToken() {
        do {
            try NemotronAccess.token.save(tokenEntry)
            tokenEntry = ""
            redacted = NemotronAccess.token.redacted()
            message = "Token saved to your Keychain — checking it now."
            onCheck()
        } catch {
            message = error.localizedDescription
        }
    }
}
