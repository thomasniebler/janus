import SwiftUI

struct ChatView: View {
    @EnvironmentObject private var chatStore: ChatStore
    @State private var inputText = ""

    private var trimmedInput: String {
        inputText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(spacing: 0) {
            if let error = chatStore.errorMessage {
                HStack {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                    Spacer()
                    Button("Reconnect") {
                        chatStore.connect()
                    }
                    .font(.footnote)
                }
                .padding(.horizontal)
                .padding(.vertical, 6)
                .background(Color.red.opacity(0.08))
            }

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        if chatStore.messages.isEmpty {
                            Text("Ask the Janus agent about your schedule, tasks, or inbox.")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.top, 40)
                        }
                        ForEach(chatStore.messages) { message in
                            ChatBubble(message: message)
                                .id(message.id)
                        }
                    }
                    .padding()
                }
                .onChange(of: chatStore.messages.count) { _, _ in
                    guard let last = chatStore.messages.last else { return }
                    withAnimation {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }

            Divider()

            HStack(alignment: .bottom, spacing: 8) {
                TextField("Ask Janus…", text: $inputText, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1 ... 4)
                    .disabled(chatStore.isStreaming)
                Button {
                    let text = trimmedInput
                    inputText = ""
                    Task { await chatStore.send(text) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(trimmedInput.isEmpty || chatStore.isStreaming)
                .accessibilityLabel("Send message")
            }
            .padding()
        }
        .navigationTitle("Chat")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    chatStore.startNewConversation()
                } label: {
                    Image(systemName: "square.and.pencil")
                }
                .accessibilityLabel("Start a new chat")
            }
        }
        .onAppear { chatStore.connect() }
        .onDisappear { chatStore.disconnect() }
    }
}

private struct ChatBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            Text(message.content.isEmpty ? "…" : message.content)
                .padding(10)
                .background(message.role == .user ? Color.accentColor : Color(.secondarySystemBackground))
                .foregroundStyle(message.role == .user ? Color.white : Color.primary)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            if message.role == .assistant { Spacer(minLength: 40) }
        }
    }
}
