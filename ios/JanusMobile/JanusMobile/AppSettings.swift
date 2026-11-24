import Foundation
import Combine

@MainActor
final class AppSettings: ObservableObject {
    static let apiBaseURLKey = "janus.apiBaseURL"
    static let defaultAPIBaseURL = "http://localhost:8080"

    @Published var apiBaseURL: String {
        didSet {
            UserDefaults.standard.set(apiBaseURL, forKey: Self.apiBaseURLKey)
        }
    }

    init() {
        self.apiBaseURL = UserDefaults.standard.string(forKey: Self.apiBaseURLKey) ?? Self.defaultAPIBaseURL
    }

    var normalizedBaseURL: String? {
        let trimmed = apiBaseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.isValidBaseURL(trimmed) else {
            return nil
        }
        return trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
    }

    static func isValidBaseURL(_ value: String) -> Bool {
        guard !value.isEmpty else {
            return false
        }
        guard let url = URL(string: value), let scheme = url.scheme else {
            return false
        }

        let normalizedScheme = scheme.lowercased()
        if normalizedScheme == "https" {
            return true
        }
        if normalizedScheme != "http" {
            return false
        }

        guard let host = url.host(percentEncoded: false)?.lowercased() else {
            return false
        }
        return host == "localhost" || host == "127.0.0.1" || host == "::1"
    }
}
