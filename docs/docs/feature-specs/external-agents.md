# External Agents

**Status:** Shipped (local coding agents and remote A2A agents; Janus as an A2A server is out of scope)
**Linear:** JAN-403 (coding agents tool); JAN-404 (A2A client)
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
| Agent2Agent (A2A) protocol | Any remote agent that publishes an agent card | Over HTTPS | Shipped (JAN-404) |

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
- **Read-only by default.** Each preset has a read-only form and an edit
  form, used only with `coding_agents_allow_edits`. Codex: `--sandbox
  read-only` / `workspace-write`. Claude Code: `--tools Read,Grep,Glob` /
  `--tools Read,Grep,Glob,Edit,Write` with `--strict-mcp-config`, plus
  `--permission-mode dontAsk` / `acceptEdits`. A permission mode alone is not
  enough, because saved allow rules in the user's Claude settings still apply
  under `dontAsk` (found in review); restricting the tool set is the fence,
  and Claude Code never gets a shell from Janus. The Claude Code preset was checked against
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

## Remote A2A agents (JAN-404, shipped)

- **Registry** (`models/a2a.py`, `A2AAgent`, table `a2a_agents`;
  `services/a2a_service.py`; `api/a2a.py` at `/api/tools/a2a-agents`;
  Settings → Tools & Connections → **Remote agents**,
  `A2AAgentManager.tsx`). The record is owner-scoped: name (the user's
  label), base URL, `enabled` (default off), the bounded card, the JSON-RPC
  URL, a Fernet-encrypted bearer token (write-only), and the last error.
  - Creating an agent fetches its card right away; a failed fetch is
    recorded in `last_error` instead of failing the request.
  - The agent can be switched on only once a card was fetched. A failed
    **Refresh card** switches it off again.
  - A new base URL forgets the card and the token and switches the agent
    off, so a token never follows the URL to another host.
  - Deleting an agent drops its tool policy row.
- **Protocol** (`services/a2a_client.py`). The card is fetched from
  `/.well-known/agent-card.json`, falling back to the pre-0.3
  `/.well-known/agent.json`. The client speaks both revisions, chosen by the
  card's protocol version:

  | Card version | JSON-RPC URL | Methods | Message shape | Task states |
  |---|---|---|---|---|
  | 1.x (current) | first `JSONRPC` entry in `supportedInterfaces` | `SendMessage`, then `GetTask` | `ROLE_USER`, `{"text": ...}` parts | `TASK_STATE_*` |
  | 0.x | the card's `url` | `message/send` with `blocking`, then `tasks/get` | `kind` discriminators | lowercase states |

  - Requests carry `A2A-Version` and the bearer token.
  - A message result is the answer. Otherwise the task is polled every 2 s
    until it reaches a terminal state (`completed`, `failed`, `canceled`,
    `rejected`) or an interrupted one (`input-required`, `auth-required`),
    for at most 120 s. Past that, the tool reports the state it was still in.
  - The answer is built from the text parts (and JSON data parts) of the
    task's artifacts, else of its status message, capped at 20,000 characters.
- **Outbound safety.**
  - Both the base URL and the JSON-RPC URL the card names pass the MCP
    registry's SSRF check (`mcp_service.validate_mcp_endpoint`): public
    addresses over HTTPS, or HTTP on loopback only, with no credentials,
    query or fragment.
  - Redirects are not followed, and responses are capped at 1 MB.
  - HTTP 401/403 reads as a rejected token. A JSON-RPC `error` is reported
    with its message cut to 300 characters.
- **Chat tool** (`agent_toolboxes/remote_agents.py`). Each offered agent is
  one entry `a2a.<id>` with one tool `ask_agent_<id>(prompt)`.
  - Tier: `operation="mixed"`, `risk_level="high"`,
    `confirmation="required"`, `reads_untrusted_content`.
  - `enabled_when` admits the owner's `chat` steps, or their unattended runs
    under *Allow everything* (JAN-359).
  - `mcp_metadata.is_remote_tool_id` now covers `a2a.` as well as `mcp.`.
    So these ids get the remote-tool lock for unattended runs, the
    confirmation and injection escalation in `runtime_adapter`, and chat's
    widened allowlist.
  - The prompt follows the exact-preview rule (`validate_approvable_text`;
    refused if the approval preview would be cut or redacted).
  - The tool description names only the user's own label. The remote card's
    description and skills are shown in the UI as untrusted text and never
    reach the model, so a hostile card cannot steer Janus before it is called.
  - The answer comes back inside an escaped `<remote_agent_output>` block.
- **Not built:** streaming (`SendStreamingMessage`), push notifications,
  continuing a task that asked for input, file parts, and OAuth/OpenID or
  mTLS security schemes. Only a static bearer token is supported.

## Out of scope

- Janus as an A2A **server** (other agents calling Janus). Ask in a
  [feature request](https://github.com/thomasniebler/janus/issues/new) if
  you need it.
- Streaming a coding agent's progress into chat; the answer arrives when the
  agent finishes.
- Multi-agent swarms or boards: Janus stays a single agent.
