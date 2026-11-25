# External Agents

**Status:** Shipped (phase 1: local coding agents; remote A2A agents, JAN-404, are planned, not built)
**Linear:** JAN-403 (coding agents tool, shipped); JAN-404 (A2A client, planned)
**Depends on:** [Tool Permissions](tool-permissions.md), [MCP Tool Registry](mcp-tool-registry/feature-description.md) (registry pattern for JAN-404), the shell tool's process fences (`services/shell_service.py`, JAN-353), [LLM and agent safety](../tech-spec/llm-agent-safety.md)

## Idea

Janus stays **one agent with different facets**: there are no agent
definitions or handoffs (removed in JAN-169/JAN-227). It should still be able
to **ask other agents for help**, including agents that are not under the
user's control, much as it already calls MCP servers. To Janus, an external
agent is a tool: Janus writes a self-contained prompt, the user approves it,
and the answer comes back as untrusted tool output. Janus stays responsible
for what it does with that answer.

Two transports:

| Transport | Example agents | Where | Status |
|---|---|---|---|
| Local coding-agent CLI | Claude Code (`claude -p`), OpenAI Codex CLI (`codex exec`) | The Janus host, with the user's own CLI logins | Shipped (JAN-403) |
| Agent2Agent (A2A) protocol | Any remote agent that publishes an agent card | Over HTTPS | Planned (JAN-404) |

## Acknowledgement

This feature, and several backlog ideas (a Telegram connector, JAN-405;
HMAC-signed inbound webhooks as a trigger event, JAN-406; a skill proposer
that spots repeated tool sequences, JAN-407; token and cost tracking,
JAN-408), are inspired by
[samalgotrader7-ops/janus](https://github.com/samalgotrader7-ops/janus), an
unrelated MIT-licensed project that happens to share the name. It has had no
activity since May 2026. We reimplement the ideas against this repository's
architecture (SQLite, the tool registry, `tool_policy`, the confirmation
gate). No code was copied.

## Local coding agents (JAN-403, shipped)

One admin-only, operator-opt-in toolbox, `coding_agents`, with one tool,
`ask_coding_agent(agent, prompt)`.

- **Two supported agents, as presets.** Each agent is a fixed argument
  vector with exactly one `{prompt}` element. The prompt goes in as that
  single argument and never passes through a shell. Supported: `claude` and
  `codex`. `coding_agents_commands` can adjust either command (for a CLI
  version whose flags differ; used as written in both modes, and labelled a
  custom command to the model) but cannot add agents; a malformed entry skips
  that agent instead of breaking the toolbox. Support for other agents is a
  feature request, not configuration: we keep the list short on purpose.
  Only agents whose program is on the scrubbed `PATH` are offered, and the
  tool's `agent` argument is an enum of exactly those.
- **Read-only by default.** Each preset has a read-only form (Claude Code
  `--permission-mode dontAsk`, Codex `--sandbox read-only`) and an edit form
  (`acceptEdits`, `--sandbox workspace-write`), used only with
  `coding_agents_allow_edits`. The Claude Code preset was checked against
  Claude Code 2.1; the Codex preset follows Codex's documented `exec` flags.
- **Fences**, shared with the shell tool (`shell_service.run_argv`,
  `validate_approvable_text`):
    - the exact-preview rule for the prompt (at most 1,000 characters, no
      control or invisible characters, refused if the approval preview would
      be cut or redacted);
    - no prompt starting with `-`, which the CLIs would parse as an option
      (`--dangerously-skip-permissions`) instead of the prompt;
    - a scrubbed environment plus the operator's
      `coding_agents_env_passthrough` names (never `JANUS_*`);
    - no stdin and a fixed `coding_agents_working_dir`;
    - a timeout (`coding_agents_timeout_seconds`, default 300 s, max 3,600)
      and an output cap (`coding_agents_output_max_bytes`, default 50,000);
    - a process-group kill.
- **Tier:**
    - `operation="admin"`, `risk_level="high"`, `confirmation="required"`,
      `reads_untrusted_content`.
    - In chat, each call is approved with the exact agent and prompt.
      Unattended runs have it *Off* by default; the owner can switch it on.
      After a suspected prompt injection it is refused for the rest of the step.
    - Never delegable, not proposable, not in `LOCKED_UNATTENDED`.
    - Allowlisted for `chat` among the fixed task modes, and only inside a
      plan step.
- **Output:** a status line (agent, exit code or timeout, duration, working
  directory, read-only or edit mode), the answer inside an escaped
  `<coding_agent_output>` block, and a note that it is another agent's
  answer: data, not instructions.
- **Residual risk:** the prompt goes to the CLI's model provider under the
  user's own account. In edit mode the agent can change any file in the
  working directory. The CLIs' own sandboxes and permission modes are the
  inner fence; approving each prompt is the outer one. There is no container
  sandbox.

## Remote A2A agents (JAN-404, planned)

- A per-user registry, like the MCP registry: base URL, the fetched agent
  card, and a Fernet-encrypted credential the model never sees. The A2A
  client in the inspiring project takes the bearer token as a model argument;
  we will not do that.
- One chat tool per registered agent, `ask_agent_<name>(prompt)`, using the
  current A2A protocol (`message/send`) through the official SDK.
  Long-running remote tasks run asynchronously through the delegation runtime.
- Same tier as the coding agents: sending a prompt sends data to a third
  party, so `confirmation="required"`, *Off* unattended by default, and
  refusal after a suspected prompt injection.
- Settings UI next to the MCP servers; local coding agents may be listed
  there too.

## Out of scope

- Janus as an A2A **server** (other agents calling Janus).
- Streaming a coding agent's progress into chat; the answer arrives when the
  agent finishes.
- Multi-agent swarms or boards: Janus stays a single agent.
