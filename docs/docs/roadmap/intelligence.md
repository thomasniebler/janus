# Phase 2: The Intelligence

In this phase, Janus gains a "brain" to move beyond simple keyword matching and start understanding the context of your communications.

## Key Changes

- **Local LLM Integration:** We'll use **LangChain** and **Ollama** (or similar) to run a local model that analyzes email content.
- **Smarter Filtering:** If a rule doesn't catch an email, the LLM will analyze it to see if it's genuinely important.
- **The "Afternoon Digest":** Instead of ignoring non-important emails, Janus will summarize them into a daily briefing.

## Proposed Strategy

1.  **AI Fallback:** If `EmailProcessor` doesn't find a rule match, it sends the snippet to the LLM.
2.  **Daily Summary:** A new command/task that gathers all "Digest" emails from the last 24 hours and uses the LLM to write a concise report.
3.  **Feedback Loop:** You can "teach" Janus by confirming or correcting its AI-based decisions.

## Setup for Phase 2

You will need to have [Ollama](https://ollama.com/) or another local LLM provider running to use these features.
