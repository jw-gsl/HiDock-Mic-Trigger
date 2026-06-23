import AppKit
import Foundation
import Security
import WebKit

struct PlaudSession: Codable {
    let accountId: String
    let email: String?
    let displayName: String
    let region: String
    let accessToken: String
    let refreshToken: String?
}

extension PlaudSession {
    /// Build an updated session from an extractor's `refreshedTokens` JSON
    /// payload, or nil if there is nothing new to persist. The Plaud user
    /// token (`pld_ut`) is short-lived; the extractor refreshes it with the
    /// refresh token and reports the rotated tokens here so we can keep the
    /// Keychain copy current — otherwise the next sync reuses a stale token
    /// and the cloud returns an empty recording list. Pure (no Keychain) so
    /// it can be unit-tested.
    static func applyingRefreshedTokens(_ outData: Data, to existing: PlaudSession) -> PlaudSession? {
        guard
            let json = try? JSONSerialization.jsonObject(with: outData) as? [String: Any],
            let tokens = json["refreshedTokens"] as? [String: Any],
            let access = tokens["accessToken"] as? String, !access.isEmpty,
            access != existing.accessToken
        else { return nil }
        return PlaudSession(
            accountId: existing.accountId,
            email: existing.email,
            displayName: existing.displayName,
            region: (tokens["region"] as? String) ?? existing.region,
            accessToken: access,
            refreshToken: (tokens["refreshToken"] as? String) ?? existing.refreshToken
        )
    }
}

struct PlaudUser: Decodable {
    let email: String
    let nickname: String
}

enum PlaudAPI {
    static func baseURL(region: String) -> URL {
        switch region {
        case "eu": return URL(string: "https://api-euc1.plaud.ai")!
        case "apac": return URL(string: "https://api-apse1.plaud.ai")!
        default: return URL(string: "https://api.plaud.ai")!
        }
    }

    static func exchangeGoogleSSO(idToken: String, userArea: String, region: String, completion: @escaping (Result<(String, String?), Error>) -> Void) {
        let url = baseURL(region: region).appendingPathComponent("/auth/sso-callback")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("web", forHTTPHeaderField: "app-platform")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "sso_from": "web",
            "sso_type": "google",
            "id_token": idToken,
            "user_area": userArea.isEmpty ? "GB" : userArea
        ])
        URLSession.shared.dataTask(with: req) { _, response, error in
            if let error = error {
                completion(.failure(error))
                return
            }
            guard let http = response as? HTTPURLResponse else {
                completion(.failure(NSError(domain: "Plaud", code: 1, userInfo: [NSLocalizedDescriptionKey: "No Plaud SSO response"])))
                return
            }
            guard (200..<300).contains(http.statusCode) else {
                completion(.failure(NSError(domain: "Plaud", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "Plaud SSO failed: HTTP \(http.statusCode)"])))
                return
            }
            let headerFields = http.allHeaderFields.reduce(into: [String: String]()) { result, item in
                if let key = item.key as? String {
                    result[key] = String(describing: item.value)
                }
            }
            let cookies = HTTPCookie.cookies(withResponseHeaderFields: headerFields, for: url)
            let access = cookies.last(where: { $0.name == "pld_ut" && !$0.value.isEmpty })?.value
            let refresh = cookies.last(where: { $0.name == "pld_urt" && !$0.value.isEmpty })?.value
            guard let access else {
                completion(.failure(NSError(domain: "Plaud", code: 2, userInfo: [NSLocalizedDescriptionKey: "Plaud SSO did not return a session token"])))
                return
            }
            completion(.success((access, refresh)))
        }.resume()
    }

    static func userInfo(accessToken: String, region: String, completion: @escaping (Result<PlaudUser, Error>) -> Void) {
        let url = baseURL(region: region).appendingPathComponent("/user/me")
        var req = URLRequest(url: url)
        req.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        URLSession.shared.dataTask(with: req) { data, response, error in
            if let error = error {
                completion(.failure(error))
                return
            }
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                completion(.failure(NSError(domain: "Plaud", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "Plaud user lookup failed: HTTP \(http.statusCode)"])))
                return
            }
            guard let data else {
                completion(.failure(NSError(domain: "Plaud", code: 3, userInfo: [NSLocalizedDescriptionKey: "Empty Plaud user response"])))
                return
            }
            do {
                let raw = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                let user = (raw?["data_user"] as? [String: Any]) ?? (raw?["data"] as? [String: Any]) ?? raw ?? [:]
                let email = user["email"] as? String ?? ""
                let nickname = user["nickname"] as? String ?? "Plaud"
                completion(.success(PlaudUser(email: email, nickname: nickname)))
            } catch {
                completion(.failure(error))
            }
        }.resume()
    }
}

enum PlaudAuthStore {
    private static let service = "com.hidock.tools.plaud"

    static func save(_ session: PlaudSession) throws {
        let data = try JSONEncoder().encode(session)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: session.accountId
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = data
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(domain: "PlaudKeychain", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Could not save Plaud session to Keychain"])
        }
    }

    static func load(accountId: String) -> PlaudSession? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: accountId,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(PlaudSession.self, from: data)
    }

    static func delete(accountId: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: accountId
        ]
        SecItemDelete(query as CFDictionary)
    }
}

final class PlaudLoginWindowController: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, NSWindowDelegate {
    private let region: String
    private let log: (String) -> Void
    private let completion: (Result<PlaudSession, Error>) -> Void
    private var window: NSWindow?
    private var webView: WKWebView?
    private var childWindows: [NSWindow] = []
    private var pollTimer: Timer?
    private var completed = false
    // Ephemeral, per-login data store so every sign-in starts from a clean
    // web.plaud.ai session (mirrors the Windows app's off-the-record
    // QWebEngineProfile). Using the shared `.default()` store meant a leftover
    // `pld_ut` from a previous pairing was captured instantly by `pollCookies`,
    // so the user could never sign in as a different account and re-pairing
    // silently re-adopted the old session. We poll *this* store, not `.default()`.
    private let dataStore = WKWebsiteDataStore.nonPersistent()

    init(region: String, log: @escaping (String) -> Void = { _ in }, completion: @escaping (Result<PlaudSession, Error>) -> Void) {
        self.region = region
        self.log = log
        self.completion = completion
        super.init()
    }

    func show() {
        log("Plaud SSO: opening WebKit login window (region=\(region))")
        let config = WKWebViewConfiguration()
        config.websiteDataStore = dataStore
        config.userContentController.add(self, name: "plaud")
        config.userContentController.addUserScript(WKUserScript(source: Self.captureScript(region: region), injectionTime: .atDocumentStart, forMainFrameOnly: false))

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        self.webView = webView

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Sign in to Plaud"
        win.contentView = webView
        win.delegate = self
        self.window = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        webView.load(URLRequest(url: URL(string: "https://web.plaud.ai")!))
        log("Plaud SSO: loading https://web.plaud.ai")
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.pollCookies()
        }
    }

    func windowWillClose(_ notification: Notification) {
        guard !completed else { return }
        log("Plaud SSO: login window closed before completion")
        complete(.failure(NSError(domain: "Plaud", code: 10, userInfo: [NSLocalizedDescriptionKey: "Plaud sign-in window closed"])))
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        log("Plaud SSO: popup requested \(navigationAction.request.url?.absoluteString ?? "(unknown url)")")
        let popup = WKWebView(frame: .zero, configuration: configuration)
        popup.navigationDelegate = self
        popup.uiDelegate = self
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 640),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Plaud Sign In"
        win.contentView = popup
        childWindows.append(win)
        win.makeKeyAndOrderFront(nil)
        return popup
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let payload = message.body as? [String: Any],
              let type = payload["type"] as? String else { return }
        if type == "sso", let idToken = payload["idToken"] as? String {
            let userArea = payload["userArea"] as? String ?? "GB"
            log("Plaud SSO: captured Google id_token, exchanging with Plaud (userArea=\(userArea))")
            PlaudAPI.exchangeGoogleSSO(idToken: idToken, userArea: userArea, region: region) { [weak self] result in
                switch result {
                case .success(let tokens):
                    self?.log("Plaud SSO: Plaud exchange returned session token (refresh=\(tokens.1 == nil ? "no" : "yes"))")
                    self?.finishWithTokens(access: tokens.0, refresh: tokens.1)
                case .failure(let error):
                    self?.log("Plaud SSO: Plaud exchange failed: \(error.localizedDescription)")
                    self?.complete(.failure(error))
                }
            }
        }
    }

    private func pollCookies() {
        dataStore.httpCookieStore.getAllCookies { [weak self] cookies in
            guard let self, !self.completed else { return }
            let access = cookies.last(where: { $0.name == "pld_ut" && !$0.value.isEmpty })?.value
            let refresh = cookies.last(where: { $0.name == "pld_urt" && !$0.value.isEmpty })?.value
            if let access {
                self.log("Plaud SSO: captured Plaud session cookie from WebKit (refresh=\(refresh == nil ? "no" : "yes"))")
                self.finishWithTokens(access: access, refresh: refresh)
            }
        }
    }

    private func finishWithTokens(access: String, refresh: String?) {
        guard !completed else { return }
        log("Plaud SSO: looking up signed-in user")
        PlaudAPI.userInfo(accessToken: access, region: region) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let user):
                self.log("Plaud SSO: user lookup complete email=\(user.email.isEmpty ? "(empty)" : user.email), nickname=\(user.nickname)")
                let accountId = Self.accountId(email: user.email, accessToken: access)
                let session = PlaudSession(
                    accountId: accountId,
                    email: user.email.isEmpty ? nil : user.email,
                    displayName: "Plaud",
                    region: self.region,
                    accessToken: access,
                    refreshToken: refresh
                )
                self.complete(.success(session))
            case .failure(let error):
                self.log("Plaud SSO: user lookup failed: \(error.localizedDescription)")
                self.complete(.failure(error))
            }
        }
    }

    private func complete(_ result: Result<PlaudSession, Error>) {
        DispatchQueue.main.async {
            guard !self.completed else { return }
            switch result {
            case .success(let session):
                self.log("Plaud SSO: complete for \(session.displayName) (\(session.region))")
            case .failure(let error):
                self.log("Plaud SSO: failed: \(error.localizedDescription)")
            }
            self.completed = true
            self.pollTimer?.invalidate()
            self.pollTimer = nil
            self.childWindows.forEach { $0.close() }
            self.window?.delegate = nil
            self.window?.close()
            self.completion(result)
        }
    }

    private static func accountId(email: String, accessToken: String) -> String {
        if !email.isEmpty {
            return email.lowercased()
                .replacingOccurrences(of: "[^a-z0-9]+", with: "-", options: .regularExpression)
                .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        }
        return String(abs(accessToken.hashValue))
    }

    private static func captureScript(region: String) -> String {
        """
        (function () {
          if (window.__hidockPlaudHooked) return;
          window.__hidockPlaudHooked = true;
          function looksLikeJwt(value) {
            return typeof value === "string" && value.split(".").length === 3 && value.length > 80;
          }
          function idTokenFrom(data) {
            if (!data) return null;
            if (looksLikeJwt(data)) return data;
            const candidate = data.credential || data.id_token || data.idToken;
            return looksLikeJwt(candidate) ? candidate : null;
          }
          function finishSso(idToken, userArea) {
            if (!looksLikeJwt(idToken)) return;
            try {
              window.webkit.messageHandlers.plaud.postMessage({
                type: "sso",
                idToken: idToken,
                userArea: userArea || "GB",
                region: "\(region)"
              });
            } catch (_) {}
          }
          if (location.hostname === "accounts.google.com" && location.pathname.indexOf("gsi/transform") !== -1) {
            const capture = function (data) {
              const idToken = idTokenFrom(data);
              if (idToken) finishSso(idToken, "GB");
            };
            if (!window.opener) {
              window.opener = { closed: false, postMessage: function (data) { capture(data); } };
            } else {
              const realPost = window.opener.postMessage && window.opener.postMessage.bind(window.opener);
              window.opener.postMessage = function (data, targetOrigin) {
                capture(data);
                if (realPost) try { return realPost(data, targetOrigin); } catch (_) {}
              };
            }
          }
          const originalFetch = window.fetch;
          window.fetch = function (...args) {
            const reqUrl = typeof args[0] === "string" ? args[0] : args[0]?.url;
            try {
              if (reqUrl && String(reqUrl).indexOf("/auth/sso-callback") !== -1 && args[1] && typeof args[1].body === "string") {
                const parsed = JSON.parse(args[1].body);
                if (parsed && parsed.id_token) finishSso(parsed.id_token, parsed.user_area || "GB");
              }
            } catch (_) {}
            return originalFetch.apply(this, args);
          };
          const open = XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open = function (method, url, ...rest) {
            this.__hidockPlaudUrl = String(url || "");
            return open.call(this, method, url, ...rest);
          };
          const send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.send = function (...args) {
            try {
              if ((this.__hidockPlaudUrl || "").indexOf("/auth/sso-callback") !== -1 && typeof args[0] === "string") {
                const parsed = JSON.parse(args[0]);
                if (parsed && parsed.id_token) finishSso(parsed.id_token, parsed.user_area || "GB");
              }
            } catch (_) {}
            return send.apply(this, args);
          };
        })();
        """
    }
}
