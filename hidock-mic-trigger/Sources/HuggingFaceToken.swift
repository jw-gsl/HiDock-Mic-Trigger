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

    /// One Keychain read per process, not one per caller.
    ///
    /// Every `load()` is a `SecItemCopyMatching`, and macOS prompts for access on
    /// each one whenever the item's ACL does not match the running app — which is
    /// the case for any item created by an earlier build signed with a different
    /// identity. Callers made that painful: the Models page read it four times per
    /// render, and `inject(into:)` reads it again for every transcription
    /// subprocess. The result was an app that asked permission over and over.
    ///
    /// The token only changes through `save` / `delete` in this process, so both
    /// update the cache and the stored value can never be stale. `notFound` is
    /// cached too — a missing token is an answer, and re-asking for it is what
    /// produced repeat prompts on machines with no token at all.
    private enum Cached {
        case unread
        case notFound
        case token(String)
    }
    private static var cache: Cached = .unread
    private static let cacheLock = NSLock()

    /// Forget the cached value so the next read goes back to the Keychain. For
    /// tests, and for after an external change to the stored item.
    static func invalidateCache() {
        cacheLock.lock()
        cache = .unread
        cacheLock.unlock()
    }

    /// A Keychain access list containing only the running application.
    ///
    /// Returns nil if it cannot be built, in which case the item is saved without
    /// an explicit list — the previous behaviour. Failing to set an ACL is worth
    /// degrading for; failing to save the token is not.
    private static func selfOnlyAccess() -> SecAccess? {
        var me: SecTrustedApplication?
        // nil path = the current application.
        guard SecTrustedApplicationCreateFromPath(nil, &me) == errSecSuccess,
              let me else { return nil }
        var access: SecAccess?
        guard SecAccessCreate(
            "HiDock Hugging Face token" as CFString, [me] as CFArray, &access
        ) == errSecSuccess else { return nil }
        return access
    }

    /// True when the stored item can be read without prompting.
    ///
    /// `kSecUseAuthenticationUIFail` makes the Keychain return an error instead of
    /// showing a dialog, so this can be asked safely. A false here means the
    /// item's access list no longer matches this build and the only fix is to
    /// re-create it — which is what the Settings page offers.
    static func isReadableWithoutPrompting() -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
            kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        // errSecItemNotFound means there is nothing stored, which prompts nobody.
        return status == errSecSuccess || status == errSecItemNotFound
    }

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
        // Give the item an access list naming *this* application, so reading it
        // back never raises a prompt.
        //
        // Without an explicit list the item inherits whatever the creating
        // process was, and once the app is rebuilt and re-signed that entry can
        // stop matching — which is how a token saved on 2026-07-31 ended up
        // asking permission on every read afterwards, with no way to stop it from
        // inside the app. `SecAccessCreate` is long-deprecated but is still the
        // only way for a non-sandboxed app to say this; the modern alternative
        // (`kSecUseDataProtectionKeychain`) needs a keychain-access-group
        // entitlement this app does not carry.
        if let access = selfOnlyAccess() {
            add[kSecAttrAccess as String] = access
        }
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else {
            invalidateCache()
            throw NSError(
                domain: "HuggingFaceKeychain", code: Int(status),
                userInfo: [NSLocalizedDescriptionKey:
                    "Could not save the Hugging Face token to the Keychain"]
            )
        }
        // Delete-then-add re-creates the item, so its ACL now belongs to *this*
        // app. That is what clears the repeat-prompt state left by an item an
        // earlier, differently-signed build created.
        cacheLock.lock()
        cache = .token(trimmed)
        cacheLock.unlock()
    }

    static func load() -> String? {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        switch cache {
        case .notFound: return nil
        case .token(let token): return token
        case .unread: break
        }
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
        else {
            cache = .notFound
            return nil
        }
        cache = .token(token)
        return token
    }

    static func delete() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        cacheLock.lock()
        cache = .notFound
        cacheLock.unlock()
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
