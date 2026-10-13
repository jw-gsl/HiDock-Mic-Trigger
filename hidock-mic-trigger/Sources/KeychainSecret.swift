import Foundation

/// A single secret in the login Keychain, readable by this app without
/// prompting. The same storage rules as `HuggingFaceToken` (see there for the
/// history): an access list naming only this app, one Keychain read per
/// process, and a one-time repair when an item made by a differently-signed
/// build would otherwise prompt on every read.
final class KeychainSecret {
    let service: String
    let account: String
    let label: String

    private enum Cached { case unread, notFound, value(String) }
    private var cache: Cached = .unread
    private let lock = NSLock()

    init(service: String, account: String, label: String) {
        self.service = service
        self.account = account
        self.label = label
    }

    private var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private func selfOnlyAccess() -> SecAccess? {
        var me: SecTrustedApplication?
        guard SecTrustedApplicationCreateFromPath(nil, &me) == errSecSuccess, let me else { return nil }
        var access: SecAccess?
        guard SecAccessCreate(label as CFString, [me] as CFArray, &access) == errSecSuccess else { return nil }
        return access
    }

    func isReadableWithoutPrompting() -> Bool {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        query[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    func save(_ secret: String) throws {
        let trimmed = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { delete(); return }
        SecItemDelete(baseQuery as CFDictionary)
        var add = baseQuery
        add[kSecValueData as String] = Data(trimmed.utf8)
        if let access = selfOnlyAccess() { add[kSecAttrAccess as String] = access }
        let status = SecItemAdd(add as CFDictionary, nil)
        lock.lock()
        cache = status == errSecSuccess ? .value(trimmed) : .unread
        lock.unlock()
        guard status == errSecSuccess else {
            throw NSError(domain: "KeychainSecret", code: Int(status),
                          userInfo: [NSLocalizedDescriptionKey: "Could not save \(label) to the Keychain"])
        }
    }

    func load() -> String? {
        lock.lock()
        defer { lock.unlock() }
        switch cache {
        case .notFound: return nil
        case .value(let value): return value
        case .unread: break
        }
        let neededPrompt = !isReadableWithoutPrompting()
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        var value: String?
        if SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
           let data = result as? Data, let text = String(data: data, encoding: .utf8), !text.isEmpty {
            value = text
        }
        if let value, neededPrompt, let access = selfOnlyAccess() {
            // Re-create so this build owns the item and stops prompting.
            SecItemDelete(baseQuery as CFDictionary)
            var add = baseQuery
            add[kSecValueData as String] = Data(value.utf8)
            add[kSecAttrAccess as String] = access
            _ = SecItemAdd(add as CFDictionary, nil)
        }
        cache = value.map(Cached.value) ?? .notFound
        return value
    }

    func delete() {
        SecItemDelete(baseQuery as CFDictionary)
        lock.lock()
        cache = .notFound
        lock.unlock()
    }

    /// Enough to recognise which secret is stored without showing it.
    func redacted() -> String? {
        guard let value = load() else { return nil }
        guard value.count > 10 else { return "•••" }
        return "\(value.prefix(4))…\(value.suffix(4))"
    }
}
