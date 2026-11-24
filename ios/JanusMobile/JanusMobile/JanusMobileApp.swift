import SwiftUI

@main
struct JanusMobileApp: App {
    @StateObject private var settings: AppSettings
    @StateObject private var sessionStore: SessionStore
    @StateObject private var chatStore: ChatStore

    init() {
        let appSettings = AppSettings()
        let client = APIClient(settings: appSettings)
        _settings = StateObject(wrappedValue: appSettings)
        _sessionStore = StateObject(wrappedValue: SessionStore(client: client))
        _chatStore = StateObject(wrappedValue: ChatStore(settings: appSettings))
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(settings)
                .environmentObject(sessionStore)
                .environmentObject(chatStore)
                .task {
                    await sessionStore.bootstrap()
                }
        }
    }
}
