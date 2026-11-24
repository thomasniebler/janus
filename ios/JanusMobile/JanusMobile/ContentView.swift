import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var sessionStore: SessionStore
    @EnvironmentObject private var chatStore: ChatStore
    @State private var showingSettings = false

    var body: some View {
        NavigationStack {
            Group {
                if sessionStore.currentUser == nil {
                    LoginView()
                } else {
                    TabView {
                        DashboardView()
                            .tabItem { Label("Dashboard", systemImage: "tray.full") }
                        ChatView()
                            .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
                    }
                }
            }
            .navigationTitle("Janus")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings") {
                        showingSettings = true
                    }
                    .accessibilityLabel("Open Settings")
                }
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView()
            }
            .onChange(of: sessionStore.currentUser == nil) { _, loggedOut in
                // Never let a chat session outlive its login: a signed-out user
                // (or one who signs in as someone else afterward) must not see
                // the previous account's conversation.
                if loggedOut {
                    chatStore.reset()
                }
            }
        }
    }
}

private struct LoginView: View {
    @EnvironmentObject private var sessionStore: SessionStore
    @State private var username = ""
    @State private var password = ""

    var body: some View {
        Form {
            Section("Sign In") {
                TextField("Username", text: $username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Password", text: $password)
                Button("Sign In") {
                    Task {
                        await sessionStore.login(username: username, password: password)
                    }
                }
                .disabled(sessionStore.isLoading || username.isEmpty || password.isEmpty)
            }

            if let error = sessionStore.errorMessage {
                Section("Error") {
                    Text(error)
                        .foregroundStyle(.red)
                }
            }
        }
    }
}

private struct DashboardView: View {
    @EnvironmentObject private var sessionStore: SessionStore

    var body: some View {
        List {
            if let user = sessionStore.currentUser {
                Section("Session") {
                    Text("Signed in as \(user.username)")
                }
            }

            if !(sessionStore.dashboard?.events.isEmpty ?? true) {
                Section("Today's Schedule") {
                    ForEach(sessionStore.dashboard?.events ?? []) { event in
                        EventRow(event: event)
                    }
                }
            }

            if !(sessionStore.dashboard?.tasks.isEmpty ?? true) {
                Section("Tasks") {
                    ForEach(sessionStore.dashboard?.tasks ?? []) { task in
                        TaskRow(task: task)
                    }
                }
            }

            Section("Important Emails") {
                ForEach(sessionStore.dashboard?.importantEmails ?? []) { email in
                    EmailRow(email: email)
                }
            }

            Section("Recent Emails") {
                ForEach(sessionStore.dashboard?.allEmails ?? []) { email in
                    EmailRow(email: email)
                }
            }
        }
        .overlay {
            if sessionStore.isLoading && sessionStore.dashboard == nil {
                ProgressView("Loading…")
            }
        }
        .refreshable {
            await sessionStore.refreshDashboard()
        }
        .toolbar {
            ToolbarItemGroup(placement: .bottomBar) {
                Button("Refresh") {
                    Task { await sessionStore.refreshDashboard() }
                }
                .accessibilityLabel("Refresh dashboard")
                Spacer()
                Button("Sign Out") {
                    Task { await sessionStore.logout() }
                }
                .accessibilityLabel("Sign out")
            }
        }
    }
}

private struct EmailRow: View {
    let email: Email

    private var priorityColor: Color {
        switch email.priority {
        case "important": return .red
        case "digest": return .orange
        case "spam": return .gray
        default: return .secondary
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(priorityColor)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 4) {
                Text(email.subject)
                    .font(.headline)
                Text(email.sender)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text(email.snippet)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }
}

private struct EventRow: View {
    let event: PlannerEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(event.title)
                .font(.headline)
            if let location = event.location, !location.isEmpty {
                Text(location)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct TaskRow: View {
    let task: PlannerTask

    var body: some View {
        HStack {
            Text(task.title)
                .font(.body)
            Spacer()
            Text(task.status)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var sessionStore: SessionStore
    @EnvironmentObject private var chatStore: ChatStore
    @State private var apiURL = ""
    private var trimmedApiURL: String {
        apiURL.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    private var canSave: Bool {
        AppSettings.isValidBaseURL(trimmedApiURL)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Janus API URL") {
                    TextField("https://janus.example.com", text: $apiURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        settings.apiBaseURL = trimmedApiURL
                        sessionStore.resetSessionForBaseURLChange()
                        chatStore.reset()
                        dismiss()
                    }
                    .disabled(!canSave)
                }
            }
            .onAppear {
                apiURL = settings.apiBaseURL
            }
        }
    }
}
