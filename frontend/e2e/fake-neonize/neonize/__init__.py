"""Scripted stand-in for the Neonize client library, used only by the Playwright e2e backend.

`frontend/e2e/env.ts` puts `frontend/e2e/fake-neonize` first on the e2e backend's
PYTHONPATH, so `import neonize` there loads this package instead of the real one and no
WhatsApp connection is ever opened. Everything above the client (the connector, its event
handlers, the setup service, the API, and the UI) runs unchanged.

Specs drive a client through files in `$JANUS_E2E_FAKE_NEONIZE_DIR` (see `client.py`).
"""
