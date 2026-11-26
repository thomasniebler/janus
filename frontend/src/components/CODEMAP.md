# Frontend Components Code Map

Scope: React components under `frontend/src/components/`. Behavior detail: `docs/docs/tech-spec/frontend.md`.

## Responsibilities

- reusable UI primitives (`ui/`)
- domain feature components that own their queries and mutations (`features/`)
- the three app shells and the lazy shell loader

Most components have a sibling `*.stories.tsx` and/or `*.test.tsx`; `*Fixtures.ts` files hold story/test sample data.

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `appShells.ts`, `useAppShell.ts` | Lazy app-shell registry and the hook `App.tsx` uses to load and keep the active shell |
| `layout/` | Classic (UIv1) shell (`Layout.tsx`, `AppSidebar.tsx`) plus shared nav: `useNavItems.ts` (items for every shell), `PageSectionNav.tsx` (in-page tabs), `NavBadge.tsx` |
| `layout-v2/` | UIv2 "terminal" shell (`LayoutV2.tsx`, `AppTopNav.tsx`) |
| `layout-v3/` | UIv3 "rusty console" shell (`LayoutV3.tsx`, `AppTopNavV3.tsx`) |
| `ui/` | Atomic primitives: Button, Card, Input, Combobox, Modal, `ConfirmDialog.tsx`, Badge, ErrorBoundary, FeedbackBanner |
| `features/BriefingChat.tsx` | Chat view over the tab-wide chat session; renders replies, UI blocks, delegated tasks, Janus-started markers |
| `features/ToolCallInspector.tsx`, `features/ToolApprovalCard.tsx` | Tool-call chips of a reply and the inline approve/deny card |
| `features/TurnTimeline.tsx`, `features/turnTimelineDock.ts` | Plan/step timeline of the current chat turn and its `/chat` dock context |
| `features/ToolsetSelectionNote.tsx`, `features/DelegatedTasksPanel.tsx`, `features/TypingDots.tsx` | Chat extras: tools picked for a message, delegated tasks of a conversation, typing indicator |
| `features/JanusConversationMarkers.tsx`, `features/janusConversations.ts` | "Started by Janus" markers and the query behind the Chat nav badge |
| `features/ui-blocks/` | Generative chat UI blocks; entry `UIBlockView.tsx` |
| `features/JanusMarkdown.tsx` | Markdown renderer for Janus-authored text (chat, briefing, wiki) |
| `features/Briefing*.tsx` | Briefing widget, its settings, and the Daily Briefing settings tab |
| `features/SecretarySettings.tsx`, `features/DeviceAccessSettings.tsx` | Secretary card (waking hours, WhatsApp alerts) and device-token management |
| `features/Dashboard*.tsx` | Dashboard task tree, schedule list, widget menu |
| `features/Email*.tsx`, `features/emailPriority.ts` | Email list, row, detail, priority actions, clarification queue |
| `features/Topic*.tsx` | Topic management, dashboard focus widget, badges |
| `features/MemoryManager.tsx` | Durable memory facts |
| `features/Knowledge*.tsx` | Memory & Knowledge: documents, knowledge graph/entities, search, graph view |
| `features/Wiki*.tsx`, `features/wikiLinkTarget.ts` | Wiki tab, page view, provenance, `?wiki=` deep-link resolution |
| `features/RuleManager.tsx` | Regex rules |
| `features/CharacterProfileManager.tsx`, `features/characterState/` | Character profile and adaptive character state (entry `CharacterStateManager.tsx`) |
| `features/SkillManager.tsx`, `features/SkillSuggestions.tsx` | Skill library and suggested skills |
| `features/LLMConfig.tsx`, `features/LLMModelSelect.tsx`, `features/GitHubModelsAzureFields.tsx` | Default LLM provider settings and model picker |
| `features/AdditionalModelSettings.tsx`, `features/SurfaceModelConfig.tsx`, `features/ModelChoiceFields.tsx`, `features/EmbeddingConfig.tsx`, `features/ImageGenerationConfig.tsx` | Optional model cards: per-surface models, embeddings, image generation |
| `features/AccountManager.tsx`, `features/AccountCard.tsx`, `features/AccountFormModal.tsx`, `features/PasswordChangeCard.tsx` | Mailboxes and password change |
| `features/ConnectionsConfig.tsx`, `features/*Config.tsx` | Per-integration settings (Google/Microsoft, GitHub, Linear, Telegram, web search, mail ingestion, self-evolution) |
| `features/MCPRegistryManager.tsx` | Tools & Connections: MCP servers; hosts `ToolPermissions.tsx`, `A2AAgentManager.tsx`, `InjectionClassifierCard.tsx` |
| `features/ToolIntegrationSettings.tsx` | Admin integration cards (web search, self-evolution) |
| `features/WhatsAppAccountManager.tsx`, `features/whatsappSetupSession.ts` | WhatsApp accounts and pairing |
| `features/AppearanceSettings.tsx` | UI shell switcher |
| `features/WhatsNewFlash.tsx`, `features/VersionLabel.tsx`, `features/ChangelogLink.tsx` | Post-update release-notes flash and version/changelog link |
| `features/RuntimeTriggerManager.tsx` | Settings → Triggers surface |
| `features/triggers/` | Trigger definitions editor, event picker/filters, run history, suggested automations, webhooks, run widgets |
| `features/tasks/` | Agent Tasks page: list, detail, step trace, status badge |
| `features/planner/` | Planner page calendar pickers and local events/tasks |
| `features/stats/` | Stats page cards and charts |

## Read-next guidance

- Shell layout or navigation -> `layout/`, `layout-v2/`, `layout-v3/`; nav items -> `layout/useNavItems.ts`
- Adding or renaming a shell -> `../contexts/uiVersion.ts`, `appShells.ts`, `frontend/public/ui-shell-bootstrap.js`
- Chat session and socket -> `../contexts/ChatSessionContext.tsx`; frame parsing -> `../lib/toolCallEvents.ts`, `../lib/planSteps.ts`, `../lib/uiBlocks.ts`
- Page snapshot sent with chat / page introspection -> `../lib/pageContext.ts`, `../hooks/pageContextSections.ts`, `../lib/uiOutline.ts`, `../lib/uiIntrospection.ts`
- Where each shell mounts the chat -> the shell file, `../hooks/useMediaQuery.ts`, `../test/shellChatPlacement.tsx`
- Wiki links and knowledge graph data -> `../lib/wikiLinks.ts`, `../lib/knowledgeStack.ts`
- Trigger status colours and widget styles -> `features/triggers/triggerPresentation.ts`
- Confirm prompts -> `ui/ConfirmDialog.tsx` via `../hooks/useConfirmDialog.tsx`
