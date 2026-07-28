import Foundation

/// Keychain storage for a Hugging Face access token.
///
/// pyannote's diarization models are *gated*: accepting the licence grants your
/// account access, but a programmatic download still has to authenticate as you.
/// Without a token `Pipeline.from_pretrained` returns 401 — and for some paths
/// returns `nil` rather than raising, which reads as success and fails much later.
///
/// Deliberately Keychain rather than a `.env` file. A token is a credential that
/// can read every gated repo the account can reach; writing it into the repo
/// working tree risks it reaching a commit, a backup, or a support log. It is
/// handed to the Python pipeline through the subprocess environment instead, so
/// it never lands on disk in plaintext.
enum HuggingFaceToken {
    private static let service = "com.hidock.tools.huggingface"
    private static let account = "hf-access-token"

    /// The licence that must be accepted before the token can fetch the model.
    /// Free for research and commercial use — the gate is usage tracking.
    static let licenceURL = URL(
        string: "https://huggingface.co/pyannote/speaker-diarization-community-1"
    )!
    /// Where a token is created. Read access is sufficient.
    static let tokenSettingsURL = URL(string: "https://huggingface.co/settings/tokens")!

    static func save(_ token: String) throws {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            delete()
            return
        }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = Data(trimmed.utf8)
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(
                domain: "HuggingFaceKeychain", code: Int(status),
                userInfo: [NSLocalizedDescriptionKey:
                    "Could not save the Hugging Face token to the Keychain"]
            )
        }
    }

    static func load() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let token = String(data: data, encoding: .utf8),
              !token.isEmpty
        else { return nil }
        return token
    }

    static func delete() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }

    static var isConfigured: Bool { load() != nil }

    /// Never show a credential in full. Enough to confirm *which* token is
    /// stored without putting it on screen or into a screenshot.
    static func redacted() -> String? {
        guard let token = load() else { return nil }
        guard token.count > 10 else { return "•••" }
        return "\(token.prefix(6))…\(token.suffix(4))"
    }

    /// Add the token to a subprocess environment, if one is stored.
    ///
    /// `HF_TOKEN` is what huggingface_hub reads; the pipeline also accepts the
    /// older names, so all three are set for robustness across library versions.
    static func inject(into environment: inout [String: String]) {
        guard let token = load() else { return }
        environment["HF_TOKEN"] = token
        environment["HUGGING_FACE_HUB_TOKEN"] = token
        environment["HUGGINGFACE_TOKEN"] = token
    }
}
