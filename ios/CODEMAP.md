# iOS Code Map

Scope: native SwiftUI client scaffold in `ios/` (login, dashboard widgets, `/ws/chat`). Details: `docs/docs/tech-spec/mobile-ios.md`.

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `JanusMobile/project.yml` | XcodeGen project definition |
| `JanusMobile/JanusMobile/` | App source: models, API client, views |
| `JanusMobile/JanusMobile/ChatStore.swift` | `/ws/chat` client |
| `JanusMobile/JanusMobile/TokenStore.swift` | Keychain JWT storage |
| `README.md` | Build and run instructions |
