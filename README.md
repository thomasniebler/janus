# Janus 🦉

Personal AI Assistant for filtering noise and surfacing signal.

## Development

This project uses `uv` for dependency management and `prek` (a Rust-based `pre-commit` alternative) for code consistency.

### Setup

1. Install dependencies:
   ```bash
   uv sync
   ```
   WhatsApp, the system tray icon, and PDF/Office document parsing are optional
   extras. Add the ones you want, for example
   `uv sync --extra whatsapp --extra desktop --extra documents`. See the
   [Quickstart](docs/docs/quickstart.md#optional-features) for what each one adds.

2. Create your local environment file:
   ```bash
   cp .env.example .env
   ```
   Then update `.env` with your local secrets and database connection details.

3. Install git hooks:
   ```bash
   uv run prek install
   ```

### Code Consistency

The hooks will run automatically before each commit. You can also run them manually:

```bash
uv run prek run --all-files
```

The following hooks are configured:
- **Linting & Formatting**: `ruff` (via `uv run`)
- **Dead Code**: `vulture` and `deptry` for the backend, `knip` for the frontend.
- **Branch Protection**: Prevents direct commits to `main`.
- **File Integrity**: Newline checks, trailing whitespace, YAML/TOML validation.
- **Formatters**: `prettier` (YAML/JSON) and `toml-sort`.

### Test Coverage

Backend coverage is measured with `pytest-cov`.

```bash
task coverage
```

Current backend line coverage for `src/janus` is **76%** based on the full test suite run on 2026-05-11.

### CI quality gates

Pull requests run the following checks (a draft PR only the cheap ones; the test suites run once it is ready for review), and a nightly run checks `main` with all of them. Run the same commands locally before opening a pull request:

```bash
# Backend dependencies (with the optional extras, as CI), lint, formatting, dead code, and tests with coverage
uv sync --frozen --extra documents --extra whatsapp --extra desktop
task lint
uv run ruff format --check .
task lint:dead:backend
task coverage

# Frontend tests, lint, dead code, and production build
cd frontend
npm ci --no-audit --no-fund
npm run test
npm run lint
npm run knip:ci
npm run build
cd ..

# Documentation build
task docs
```

### OpenAPI schema and frontend API types

The backend's OpenAPI schema and the TypeScript types generated from it are committed under `frontend/src/generated/` (`openapi.json`, `api.d.ts`). Generation is offline: it imports the FastAPI app without starting the server or any worker, needs no running backend or network, and works with the public API docs disabled.

```bash
task openapi:export     # backend schema -> frontend/src/generated/openapi.json
task openapi:types      # openapi.json -> frontend/src/generated/api.d.ts (npm run generate:types)
task openapi:generate   # both; run after changing a route or a request/response schema
task openapi:check      # fail when either file is out of date (CI job openapi-check)
```

## iOS Client

A native SwiftUI iOS app scaffold is available under `ios/`.

```bash
cd ios/JanusMobile
xcodegen generate
open JanusMobile.xcodeproj
```

The Janus API base URL is configurable from the in-app Settings screen.

## License

Janus is released under the [MIT License](LICENSE).
