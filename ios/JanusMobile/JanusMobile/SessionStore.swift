import Foundation
import Combine

@MainActor
final class SessionStore: ObservableObject {
    @Published private(set) var currentUser: User?
    @Published private(set) var dashboard: DashboardData?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func bootstrap() async {
        do {
            let user = try await client.getCurrentUser()
            currentUser = user
            await refreshDashboard()
        } catch APIClientError.unauthorized {
            currentUser = nil
        } catch {
            // Surface connectivity/configuration issues (unlike a plain "not logged
            // in yet" 401) so a misconfigured server URL is diagnosable from the
            // login screen rather than failing silently.
            currentUser = nil
            errorMessage = error.localizedDescription
        }
    }

    func login(username: String, password: String) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            try await client.login(username: username, password: password)
            currentUser = try await client.getCurrentUser()
            await refreshDashboard()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func logout() async {
        do {
            try await client.logout()
        } catch {
            errorMessage = error.localizedDescription
        }
        currentUser = nil
        dashboard = nil
        client.clearSession()
    }

    func refreshDashboard() async {
        guard currentUser != nil else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            dashboard = try await client.getDashboard()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func resetSessionForBaseURLChange() {
        currentUser = nil
        dashboard = nil
        errorMessage = nil
        client.clearSession()
    }
}
