import Foundation

enum APIClientError: Error, LocalizedError {
    case invalidBaseURL
    case invalidResponse
    case unauthorized
    case server(String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            return "Set a valid Janus API URL in Settings."
        case .invalidResponse:
            return "The Janus server returned an invalid response."
        case .unauthorized:
            return "Authentication failed."
        case let .server(message):
            return message
        }
    }
}

final class APIClient {
    private let settings: AppSettings
    private let session: URLSession

    init(settings: AppSettings, session: URLSession = .shared) {
        self.settings = settings
        self.session = session
    }

    func login(username: String, password: String) async throws {
        let payload = LoginRequest(username: username, password: password)
        let data = try await send(path: "/api/auth/login", method: "POST", body: payload)
        // The session cookie (used for REST calls) is set automatically by
        // URLSession. Also keep the raw token for the chat WebSocket, whose
        // handshake authenticates via an Authorization header instead.
        let response = try JSONDecoder().decode(LoginResponse.self, from: data)
        TokenStore.save(response.accessToken)
    }

    func getCurrentUser() async throws -> User {
        let data = try await send(path: "/api/auth/me", method: "GET")
        return try JSONDecoder().decode(User.self, from: data)
    }

    func getDashboard() async throws -> DashboardData {
        let data = try await send(path: "/api/dashboard/", method: "GET")
        return try JSONDecoder().decode(DashboardData.self, from: data)
    }

    func logout() async throws {
        _ = try await send(path: "/api/auth/logout", method: "POST")
    }

    func clearSession() {
        if let cookies = HTTPCookieStorage.shared.cookies {
            for cookie in cookies {
                HTTPCookieStorage.shared.deleteCookie(cookie)
            }
        }
        TokenStore.clear()
    }

    private func send<T: Encodable>(path: String, method: String, body: T) async throws -> Data {
        try await send(path: path, method: method, bodyData: JSONEncoder().encode(body))
    }

    private func send(path: String, method: String, bodyData: Data? = nil) async throws -> Data {
        guard let baseURL = settings.normalizedBaseURL, let url = URL(string: baseURL + path) else {
            throw APIClientError.invalidBaseURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = bodyData
        if bodyData != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }

        if httpResponse.statusCode == 401 {
            throw APIClientError.unauthorized
        }
        guard (200 ... 299).contains(httpResponse.statusCode) else {
            let message = String(data: data, encoding: .utf8)
                ?? "The server returned an error (HTTP \(httpResponse.statusCode)). Please try again."
            throw APIClientError.server(message)
        }
        return data
    }
}
