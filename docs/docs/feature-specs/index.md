# Planned work

Feature specs exist only for work that has not shipped. Once a feature ships, its behavior is described in the user guide and the [technical specification](../tech-spec/index.md), and its spec is removed.

Each spec starts with a `Status` / `Linear` / `Depends on` block; see [Delivery artifacts](../tech-spec/delivery-artifacts.md).

## Specs

- [Migrate issue tracking from Linear to GitHub Issues](github-issues-migration.md)

## Known gaps

Gaps verified in the code, without a spec yet:

- **Azure DevOps connector:** the `ado` planner provider exists, but no connector is behind it, so an ADO source never syncs.
- **O365 planner provider:** the `o365` provider exists without a connector; Outlook calendars come from the `microsoft_calendar` connection instead.
- **iOS client:** no tool approval cards, push notifications, memory, tasks or planner; it covers login, a read-only dashboard and chat.
- **LLM cost tracking:** Janus counts calls and tokens per day, surface and model, but shows no money, balances or budgets.
- **Topic preferences:** per-user topic tracking preferences exist with defaults, but have no settings page or API.
- **Bounded exploration:** the character profile's exploration rate is stored, but nothing uses it.
- **Chatting over Telegram:** Telegram messages only start triggers; you cannot talk to Janus there.
- **Remote tools in trigger runs:** remote MCP and A2A tools reach unattended runs only under **Allow everything**, not through a per-tool switch (JAN-344).
- **Skill refinement:** skill suggestions draft new skills; refining existing skills against past runs is not built.
- **Action inbox:** no APNs push and no quick reply from a notification.
- **Skills page permissions:** the `skills` toolbox is admin-only, but the Settings → Skills API lets any signed-in user change the shared library.
