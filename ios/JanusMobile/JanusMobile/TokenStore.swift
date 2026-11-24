import Foundation
import Security

/// Keychain-backed storage for the JWT the backend issues on login.
///
/// REST calls authenticate via the httponly session cookie that `URLSession`
/// already forwards automatically. The chat WebSocket handshake needs the
/// token in an `Authorization: Bearer` header instead, since cookie
/// forwarding on a WebSocket upgrade request isn't guaranteed the way it is
/// for ordinary HTTP requests; the backend accepts either (see
/// `janus.security.get_token`).
enum TokenStore {
    private static let service = "dev.janus.mobile"
    private static let account = "accessToken"

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func save(_ token: String) {
        guard let data = token.data(using: .utf8) else { return }
        SecItemDelete(baseQuery as CFDictionary)
        var attributes = baseQuery
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(attributes as CFDictionary, nil)
    }

    static func load() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func clear() {
        SecItemDelete(baseQuery as CFDictionary)
    }
}
