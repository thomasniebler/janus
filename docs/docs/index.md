# Getting Started with Janus

Janus is a privacy-first, local-first personal AI agent. It runs on your own machine or server, keeps its data in a local database, and can use a local model through Ollama, so nothing has to leave your computer. Cloud models and services are optional, and you choose them.

Janus watches your mail, calendar, tasks and messages, briefs you in the morning, answers in chat, and runs automations for you in the background. Every tool it may use sits behind a switch you control.

!!! tip "New to Janus?"
    Start with the [Quickstart](quickstart.md). It takes you from installation to your first admin account, a connected model and a monitored mailbox.

## What Janus does

| Area | What you get | Read more |
|---|---|---|
| Chat | One always-on conversation in every page. Janus uses tools, shows each tool call as a chip, asks before risky actions, and can act on the page you are looking at. | [Chat](chat.md) |
| Inbox | Mail sorted into Important, Digest, Ignore and Spam by your rules and the model. The action inbox collects everything that waits for your decision. | [Inbox](inbox.md) |
| Triggers | The morning briefing, the afternoon digest, mail processing and your own scheduled or event-driven automations all run as triggers. | [Runtime triggers](runtime-triggers.md) |
| Tools | Toolboxes for mail, planner, messaging, knowledge, the web and more. Each one has an **Unattended** switch and, where it matters, an **In chat** switch. | [Tools and permissions](tools.md) |
| Knowledge | Memory facts, tracked topics, ingested documents, a knowledge graph and a wiki, all searchable from chat. | [Knowledge](knowledge.md) |
| Planner | Google Calendar and Tasks, Outlook calendars, Jira and the local Janus planner, with suggestions from your mail. | [Planner](planner.md) |
| Integrations | Google, Microsoft 365, WhatsApp, Telegram, GitHub, Linear, remote MCP servers, remote agents and webhooks. | [Integrations](integrations.md) |
| Desktop | A tray icon, a status-bar widget and the Todo Chat companion for Hyprland. | [Desktop](desktop.md) |

You choose one of three looks for the web app under **Settings → Appearance**: Classic, Terminal or Rusty Console. All three show the same pages.

## Ask Janus about Janus

Janus reads this documentation. Ask it in chat, for example "how do I connect my calendar?" or "why did my trigger say BLOCKED?", and it answers from these pages and names the page it used. You can also read the docs in the app under **Documentation**.

## Where to go next

- **Install and set up:** [Quickstart](quickstart.md).
- **Tune settings, paths and the database:** [Configuration](configuration.md).
- **Run Janus long-term, update it, back it up:** [Operations](operations.md).
- **Understand how it is built:** the [architecture overview](architecture.md) and the [technical specification](tech-spec/index.md).
