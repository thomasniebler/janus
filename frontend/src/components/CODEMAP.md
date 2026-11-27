# Components Code Map

React components; each one's stories, tests and `*Fixtures.ts` sit next to it. Behavior: `docs/docs/tech-spec/frontend.md`.

| Area | Files |
| --- | --- |
| Shell registry | `appShells.ts`, `useAppShell.ts`: lazy shell loader for `App.tsx`; ids in `../contexts/uiVersion.ts` |
| `layout/` | UIv1 classic shell, plus nav shared by every shell (`layout/useNavItems.ts`, PageSectionNav, NavBadge) |
| `layout-v2/`, `layout-v3/` | UIv2 terminal and UIv3 rusty-console shells |
| `ui/` | Atomic primitives |
| Chat (`features/`) | BriefingChat (the chat view), `features/ui-blocks/` (generative blocks), TurnTimeline, turnTimelineDock, ToolCallInspector, ToolApprovalCard, ToolsetSelectionNote, DelegatedTasksPanel, TypingDots, `features/Janus*.tsx`, janusConversations |
| Domain (`features/`) | `features/Briefing*.tsx`, `features/Dashboard*.tsx`, `features/Email*.tsx` + emailPriority, `features/Topic*.tsx`, `features/Knowledge*.tsx`, `features/Wiki*.tsx` + wikiLinkTarget, MemoryManager, RuleManager, SkillManager, SkillCatalogImport, SkillSuggestions, CharacterProfileManager, `features/characterState/` |
| Settings (`features/`) | `features/*Config.tsx`, `features/*Settings.tsx`, `features/Account*.tsx`, PasswordChangeCard, LLMModelSelect, ModelChoiceFields, GitHubModelsAzureFields, MCPRegistryManager (hosts ToolPermissions, A2AAgentManager, InjectionClassifierCard), WhatsAppAccountManager, whatsappSetupSession |
| Triggers | RuntimeTriggerManager, `features/triggers/` |
| Page folders | `features/tasks/`, `features/planner/`, `features/stats/` |
| Release notes | WhatsNewFlash, VersionLabel, ChangelogLink |

## Where to look

- Adding or renaming a shell: `../contexts/uiVersion.ts`, `appShells.ts`, `frontend/public/ui-shell-bootstrap.js`
- Chat session and socket: `../contexts/ChatSessionContext.tsx`; frame parsing in `../lib/`
- Confirm prompts: `ui/ConfirmDialog.tsx` via `../hooks/useConfirmDialog.tsx`
