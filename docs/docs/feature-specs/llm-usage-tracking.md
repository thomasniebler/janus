# LLM Usage Tracking

**Status:** Shipped (counting and display; budgets and alerts are planned, not built)
**Linear:** JAN-408
**Depends on:** `LLMService` (every chat model is built there), the owner context of `event_logger` (JAN-272), the Stats tab (JAN-258)

## Idea

Show how many model calls and tokens Janus uses, per day, per surface (chat,
trigger runs, delegated tasks, everything else) and per model. Then the user
can see what costs what, and spot a trigger that suddenly uses far more
tokens than it should. Inspired by the cost tracking of
[samalgotrader7-ops/janus](https://github.com/samalgotrader7-ops/janus) (see
[External Agents](external-agents.md#acknowledgement)).

## Counting

- **One hook.** `LLMService.__init__` calls `llm_usage.attach(self.llm, self.model_label)`. That adds a LangChain `UsageCallback` to the model's own callbacks, so every call through any path is counted without touching call sites.
- **What a call is counted under.**
  - **Owner:** `event_logger.current_owner()`. It is already bound for chat, trigger and delegated runs, mail processing and the planner sync. Calls with no owner bound are stored with `user_id = NULL` (system work) and are not shown to any user.
  - **Surface:** the `llm_usage` context variable. `/ws/chat` binds `chat` for the connection's lifetime, which includes its title and memory-suggestion calls. `unattended_execution` binds `trigger` for a trigger run's step, or `delegated` when there is no trigger id. Everything else is `other`. Both follow the call onto worker threads: toolset selection's thread and the injection classifier's pool run the model call inside `contextvars.copy_context()`, so those calls stay attributed to the user.
  - **Model:** `LLMService.model_label` (`provider/model`).
- **Tokens.** Taken from the response's `usage_metadata`, else the legacy `llm_output.token_usage`.
  - Some streamed responses carry neither. Notably, ChatOpenAI enables streamed usage only without a custom base URL, so OpenRouter and OpenAI-compatible proxies are affected.
  - Such calls are counted in `unreported_calls` with 0 tokens. The Stats card shows them, so a low token count is not mistaken for a cheap model.
- **Storage.** `models/llm_usage.py`, `LlmUsageDay`, table `llm_usage_days`, migration `a4d6b8c0e2f5`.
  - One row per user, UTC day, surface and model, unique on that key.
  - Each call upserts it: `calls`, `input_tokens`, `output_tokens` and `unreported_calls` are incremented in place, so the table grows by day, not by call.
- **Never in the way.** `llm_usage.record` uses its own short session and swallows every error. Accounting must never break or slow a model call beyond one small write.

## Display

- **API.** `GET /api/stats` (`stats_service._llm`) returns `llm` with:
  - `per_day` (calls, input and output tokens, aligned to `dates`);
  - `by_surface` and `by_model` (sorted by total tokens);
  - `totals` (including `unreported_calls`).

  Rows are kept per UTC day, so the window is the last 7, 30 or 90 **UTC** days, not the requested time zone's calendar. West of UTC, the local calendar would otherwise end a day early and hide today's calls.
- **UI.** Settings → Stats → **LLM usage** (`features/stats/LlmUsageCard.tsx`):
  - tiles for model calls, input tokens, output tokens, and calls without token counts;
  - a tokens-per-day chart;
  - tables by surface and by model.

## Not built (follow-ups)

- **Budgets and alerts.** A monthly token budget with notices at 50/80/100% (part of JAN-408's original idea) is not built in this slice.
- **Prices.** Converting tokens to money needs per-model prices, which change often and differ per provider and contract.
- **Forcing streamed usage.** `stream_usage=True` is not forced on OpenAI-compatible endpoints, because some proxies reject `stream_options`.
