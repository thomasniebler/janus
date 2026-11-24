# iOS Code Map

Scope: native iOS app scaffold in `ios/`.

## Responsibilities

- provide a SwiftUI Janus mobile client
- authenticate against the Janus backend API
- allow runtime configuration of the backend base URL
- surface the same dashboard widgets as the web app (important/recent emails,
  today's schedule, tasks) with priority-coded email rows
- chat with the Janus agent over the plan-driven `/ws/chat` runtime (streamed
  replies, one conversation at a time)

## File and submodule map

| Path | Responsibility | Read next |
| --- | --- | --- |
| `JanusMobile/project.yml` | XcodeGen project definition for generating `JanusMobile.xcodeproj` | Read directly |
| `JanusMobile/JanusMobile/` | SwiftUI app source, models, API client, and views | Read directly |
| `JanusMobile/JanusMobile/TokenStore.swift` | Keychain storage for the JWT used to authenticate the chat WebSocket | Read directly |
| `JanusMobile/JanusMobile/ChatStore.swift` | `/ws/chat` client: connection lifecycle and wire-protocol parsing | Read directly |
| `JanusMobile/JanusMobile/ChatView.swift` | Chat UI (message list, composer) | Read directly |
| `README.md` | Build/run instructions for iOS app development | Read directly |
