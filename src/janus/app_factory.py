"""FastAPI application factory for the Janus dashboard."""

import os

import markdown
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from janus import security
from janus.api import (
    a2a,
    auth,
    character,
    connections,
    conversations,
    dashboard,
    desktop,
    dev,
    docs,
    emails,
    errors,
    events,
    github,
    images,
    inbox,
    knowledge,
    linear,
    llm_models,
    memory,
    planner,
    planner_items,
    planner_sources,
    runtime_tasks,
    runtime_triggers,
    self_evolution,
    skills,
    stats,
    tools,
    topics,
    trigger_definitions,
    web_search,
    webhooks,
    whatsapp,
    wiki,
)
from janus.api import status as status_api
from janus.config import settings
from janus.request_origin import TrustedProxyHeadersMiddleware
from janus.resources import frontend_dist_dir
from janus.startup import ensure_runtime_initialized, stop_runtime_trigger_worker
from janus.websocket.briefing import register_briefing_websocket
from janus.websocket.changes import register_changes_websocket
from janus.websocket.chat import register_chat_websocket

frontend_dir = str(frontend_dist_dir())
template_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=template_dir) if os.path.exists(template_dir) else None


class RedirectException(Exception):
    """Exception raised to trigger a redirect in FastAPI."""

    def __init__(self, url: str):
        """Initialize the redirect exception."""
        self.url = url


def markdown_filter(text: str) -> str:
    """Convert Markdown text to HTML."""
    if not text:
        return ""
    return markdown.markdown(text, extensions=["extra"])


async def redirect_exception_handler(request: Request, exc: RedirectException):
    """Handle RedirectException by returning a RedirectResponse."""
    return RedirectResponse(url=exc.url, status_code=status.HTTP_303_SEE_OTHER)


async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """Answer a 422 like FastAPI does, minus each error's ``input``: it can echo secrets (tokens, passwords) back."""
    errors = [{key: value for key, value in error.items() if key != "input"} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


async def initialize_first_run_setup() -> None:
    """Upgrade the database and print the first-run setup token if needed."""
    ensure_runtime_initialized()


async def shutdown_runtime_workers() -> None:
    """Stop opt-in runtime workers during an orderly application shutdown."""
    stop_runtime_trigger_worker()


def serve_spa(request: Request, fallback: str):
    """Catch-all route to serve the React SPA for any non-API path."""
    if fallback.startswith("api") or fallback.startswith("ws"):
        raise HTTPException(status_code=404)

    safe_path = os.path.normpath(fallback).lstrip(os.sep)
    if ".." in safe_path or safe_path.startswith(os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = os.path.join(frontend_dir, safe_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)

    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    return HTMLResponse("Janus Backend is running. Frontend not found. Run 'npm run build' in the frontend directory.")


def create_fastapi_app() -> FastAPI:
    """Create and configure the Janus FastAPI dashboard app."""
    api = FastAPI(
        title="Janus Dashboard",
        docs_url="/apidocs" if settings.public_api_docs_enabled else None,
        redoc_url="/apiredoc" if settings.public_api_docs_enabled else None,
        openapi_url="/openapi.json" if settings.public_api_docs_enabled else None,
    )

    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.add_middleware(TrustedProxyHeadersMiddleware, trusted_proxy_hops=settings.trusted_proxy_hops, settings=settings)

    csrf_dependencies = [Depends(security.enforce_csrf_for_cookie_auth)]

    api.include_router(auth.router, dependencies=csrf_dependencies)
    api.include_router(character.router, dependencies=csrf_dependencies)
    api.include_router(conversations.router, dependencies=csrf_dependencies)
    api.include_router(dashboard.router, dependencies=csrf_dependencies)
    api.include_router(desktop.router, dependencies=csrf_dependencies)
    api.include_router(dev.router, dependencies=csrf_dependencies)
    api.include_router(emails.router, dependencies=csrf_dependencies)
    api.include_router(errors.router, dependencies=csrf_dependencies)
    api.include_router(events.router, dependencies=csrf_dependencies)
    api.include_router(inbox.router, dependencies=csrf_dependencies)
    api.include_router(github.router, dependencies=csrf_dependencies)
    api.include_router(images.router, dependencies=csrf_dependencies)
    api.include_router(linear.router, dependencies=csrf_dependencies)
    api.include_router(memory.router, dependencies=csrf_dependencies)
    # Before tools.router: its /api/tools prefix must not shadow /api/tools/a2a-agents.
    api.include_router(a2a.router, dependencies=csrf_dependencies)
    api.include_router(tools.router, dependencies=csrf_dependencies)
    api.include_router(connections.router, dependencies=csrf_dependencies)
    api.include_router(planner.router, dependencies=csrf_dependencies)
    api.include_router(planner_sources.router, dependencies=csrf_dependencies)
    api.include_router(planner_items.router, dependencies=csrf_dependencies)
    api.include_router(llm_models.router, dependencies=csrf_dependencies)
    api.include_router(runtime_tasks.router, dependencies=csrf_dependencies)
    api.include_router(runtime_triggers.router, dependencies=csrf_dependencies)
    api.include_router(trigger_definitions.router, dependencies=csrf_dependencies)
    api.include_router(skills.router, dependencies=csrf_dependencies)
    api.include_router(stats.router, dependencies=csrf_dependencies)
    api.include_router(topics.router, dependencies=csrf_dependencies)
    api.include_router(knowledge.router, dependencies=csrf_dependencies)
    api.include_router(wiki.router, dependencies=csrf_dependencies)
    api.include_router(web_search.router, dependencies=csrf_dependencies)
    api.include_router(self_evolution.router, dependencies=csrf_dependencies)
    api.include_router(whatsapp.router, dependencies=csrf_dependencies)
    # Its public delivery URL sends no session cookie, so the cookie-only CSRF check never applies to it.
    api.include_router(webhooks.router, dependencies=csrf_dependencies)
    api.include_router(docs.router, dependencies=csrf_dependencies)
    api.include_router(status_api.router, dependencies=csrf_dependencies)

    api.on_event("startup")(initialize_first_run_setup)
    api.on_event("shutdown")(shutdown_runtime_workers)

    if os.path.exists(frontend_dir):
        api.mount("/assets", StaticFiles(directory=os.path.join(frontend_dir, "assets")), name="assets")

    api.add_exception_handler(RedirectException, redirect_exception_handler)
    api.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    register_briefing_websocket(api)
    register_chat_websocket(api)
    register_changes_websocket(api)
    api.get("/{fallback:path}")(serve_spa)
    return api


api = create_fastapi_app()
