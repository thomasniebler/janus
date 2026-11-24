import Foundation
import Combine

struct ChatMessage: Identifiable {
    enum Role: Equatable {
        case user
        case assistant
    }

    let id = UUID()
    let role: Role
    var content: String
}

/// Drives the interactive agent chat over `/ws/chat`.
///
/// The backend's plan-driven runtime (see `janus/websocket/chat.py`) streams
/// a small text protocol over the socket: control markers prefixed with
/// `__NAME__:` or `__NAME__`, and plain text chunks that are appended to the
/// in-progress assistant reply. This mirrors the parsing the web frontend
/// does in `BriefingChat.tsx`, minus the tool-call/tool-selection UI, which
/// this first mobile version doesn't render.
@MainActor
final class ChatStore: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var isStreaming = false
    @Published var errorMessage: String?

    private let settings: AppSettings
    private var task: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var conversationId: Int?
    /// Whether the current `conversationId` has survived a completed turn.
    /// The backend assigns the id via `db.flush()` before the first reply
    /// streams, but only persists the row on `db.commit()` after `__END__`.
    /// If that first turn errors, the id was never committed and reconnecting
    /// with it gets an immediate policy-violation close, so an error before
    /// this is true must discard the id instead of reusing it.
    private var hasConfirmedTurn = false

    init(settings: AppSettings) {
        self.settings = settings
    }

    func connect() {
        disconnect()
        errorMessage = nil

        guard let base = settings.normalizedBaseURL, var components = URLComponents(string: base) else {
            errorMessage = "Set a valid Janus API URL in Settings."
            return
        }
        guard let token = TokenStore.load() else {
            errorMessage = "Sign in again to start chatting."
            return
        }

        // Capture the Origin (http/https, no path) before rewriting the scheme
        // below: the backend's TrustedProxyHeadersMiddleware rejects any
        // WebSocket handshake whose Origin isn't in `allowed_origins`, and
        // URLSessionWebSocketTask, unlike a browser, never sends one on its
        // own. This only works when the configured API origin is already
        // allow-listed on the backend (see ios/README.md).
        var originHeader: String?
        if let scheme = components.scheme, let host = components.host {
            originHeader = components.port.map { "\(scheme)://\(host):\($0)" } ?? "\(scheme)://\(host)"
        }

        components.scheme = components.scheme == "https" ? "wss" : "ws"
        // Preserve a deployment path prefix (e.g. "/janus") instead of
        // replacing it outright, matching how APIClient builds REST URLs.
        components.path += "/ws/chat"
        if let conversationId {
            components.queryItems = [URLQueryItem(name: "conversation_id", value: String(conversationId))]
        }

        guard let url = components.url else {
            errorMessage = "Set a valid Janus API URL in Settings."
            return
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let originHeader {
            request.setValue(originHeader, forHTTPHeaderField: "Origin")
        }

        let socketTask = URLSession.shared.webSocketTask(with: request)
        task = socketTask
        socketTask.resume()

        receiveTask = Task { [weak self] in
            await self?.receiveLoop()
        }
    }

    func disconnect() {
        receiveTask?.cancel()
        receiveTask = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        // A disconnect mid-reply (tab switch, new conversation, reconnect)
        // otherwise leaves this stuck true: the receive loop that would
        // normally clear it exits via cancellation, bypassing its own
        // cleanup, which left the composer disabled until a full reset().
        isStreaming = false
    }

    /// Drops the current conversation and any locally held messages, then
    /// reconnects so the next sent message starts a fresh one server-side.
    func startNewConversation() {
        conversationId = nil
        hasConfirmedTurn = false
        messages = []
        connect()
    }

    /// Clears all local chat state, e.g. on logout or a backend URL change,
    /// so the next login doesn't resume a previous session's conversation.
    func reset() {
        disconnect()
        conversationId = nil
        hasConfirmedTurn = false
        messages = []
        errorMessage = nil
        isStreaming = false
    }

    func send(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard let task else {
            errorMessage = "Not connected. Tap Reconnect and try again."
            return
        }

        messages.append(ChatMessage(role: .user, content: trimmed))

        guard let data = try? JSONEncoder().encode(["text": trimmed]),
              let json = String(data: data, encoding: .utf8) else {
            return
        }

        do {
            try await task.send(.string(json))
        } catch {
            errorMessage = "Failed to send message: \(error.localizedDescription)"
        }
    }

    private func receiveLoop() async {
        guard let task else { return }
        do {
            while true {
                let message = try await task.receive()
                if Task.isCancelled { return }
                if case let .string(text) = message {
                    handle(text)
                }
            }
        } catch {
            if !Task.isCancelled {
                isStreaming = false
                // Surface the server's close reason when there is one (e.g.
                // "Invalid Origin header" from TrustedProxyHeadersMiddleware)
                // instead of a generic POSIX/URLError description.
                if let reasonData = task.closeReason, let reason = String(data: reasonData, encoding: .utf8),
                   !reason.isEmpty {
                    errorMessage = "Chat disconnected: \(reason)"
                } else {
                    errorMessage = "Chat disconnected: \(error.localizedDescription)"
                }
            }
        }
    }

    private func handle(_ message: String) {
        if let idString = message.stripping(prefix: "__CONVERSATION_ID__:") {
            let newId = Int(idString)
            if newId != conversationId {
                hasConfirmedTurn = false
            }
            conversationId = newId
        } else if message.hasPrefix("__TOOL_SELECTION__:") {
            // Display-only tool-lifecycle metadata; not shown in this minimal client.
        } else if message == "__START__" {
            isStreaming = true
            messages.append(ChatMessage(role: .assistant, content: ""))
        } else if message == "__END__" {
            isStreaming = false
            hasConfirmedTurn = true
        } else if message.hasPrefix("__TOOL_CALL__:") || message.hasPrefix("__TOOL_RESULT__:") {
            // Tool-lifecycle markers; ignored in this minimal client.
        } else if let error = message.stripping(prefix: "__ERROR__:") {
            errorMessage = error.isEmpty ? "Chat encountered an error." : error
            isStreaming = false
            if !hasConfirmedTurn {
                // This conversation id was only ever flushed, never committed
                // (see janus/websocket/chat.py): reusing it on Reconnect would
                // hit an immediate policy-violation close. Start fresh instead.
                conversationId = nil
            }
        } else {
            appendToLastAssistantMessage(message)
        }
    }

    private func appendToLastAssistantMessage(_ token: String) {
        guard let index = messages.lastIndex(where: { $0.role == .assistant }) else { return }
        messages[index].content += token
    }
}

private extension String {
    func stripping(prefix: String) -> String? {
        hasPrefix(prefix) ? String(dropFirst(prefix.count)) : nil
    }
}
