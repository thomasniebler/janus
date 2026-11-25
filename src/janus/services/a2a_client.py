"""Minimal client for the Agent2Agent (A2A) protocol over JSON-RPC (JAN-404).

Janus only *asks* remote agents: it fetches an agent card, sends one text
message, and polls the resulting task until it ends or a deadline passes.
Both the current protocol (1.x: ``SendMessage``/``GetTask``, ``ROLE_USER``,
``TASK_STATE_*``, ``supportedInterfaces`` in the card) and the widely deployed
0.x revision (``message/send``/``tasks/get``, ``kind`` discriminators, the card's
``url``) are understood, chosen by the card's protocol version.

Every outbound URL (the card's base URL and the JSON-RPC URL the card names)
passes the MCP registry's SSRF check (:func:`mcp_service.validate_mcp_endpoint`):
public HTTPS, or HTTP on loopback only. Redirects are not followed, responses
are capped, and nothing the remote returns is trusted: it is data for the model,
spotlighted as untrusted tool output.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from janus.services import mcp_service
from janus.services.mcp_errors import MCPServiceError

CARD_PATH = "/.well-known/agent-card.json"
#: Card path of protocol revisions before 0.3.
LEGACY_CARD_PATH = "/.well-known/agent.json"
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
MAX_RESPONSE_BYTES = 1_000_000
#: How long one question may take, polling included.
TASK_DEADLINE_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 2.0
#: Longest answer handed to the model.
MAX_ANSWER_CHARS = 20_000
MAX_SKILLS = 20
_TERMINAL = {"completed", "failed", "canceled", "rejected"}
_INTERRUPTED = {"input-required", "auth-required"}
_ACCEPTED_OUTPUT = ["text/plain", "text/markdown", "application/json"]


class A2AError(ValueError):
    """A request refused before sending, or a remote agent that failed or answered badly."""


@dataclass(frozen=True)
class AgentCard:
    """The bounded subset of an agent card Janus keeps."""

    name: str
    description: str
    version: str
    protocol_version: str
    rpc_url: str
    skills: list[dict[str, str]]

    def as_json(self) -> dict[str, Any]:
        """Return the card as stored in ``A2AAgent.card``."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "protocol_version": self.protocol_version,
            "skills": self.skills,
        }


@dataclass(frozen=True)
class AgentAnswer:
    """What the remote agent answered: its text and the final task state."""

    text: str
    state: str
    truncated: bool = False


def validate_url(url: str) -> str:
    """Return the normalized URL or raise :class:`A2AError` (SSRF policy of the MCP registry)."""
    try:
        return mcp_service.validate_mcp_endpoint(url)
    except MCPServiceError as exc:
        raise A2AError(str(exc).replace("MCP endpoint", "agent URL")) from exc


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def _major(version: str) -> int:
    match = re.match(r"\s*(\d+)", version or "")
    return int(match.group(1)) if match else 0


def _client() -> httpx.Client:
    """The HTTP client for one request (a test seam: tests swap in a mock transport here)."""
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False)


def _request(method: str, url: str, *, body: dict[str, Any] | None, headers: dict[str, str]) -> Any:
    try:
        with _client() as client:  # noqa: SIM117
            with client.stream(method, url, json=body, headers=headers) as response:
                if response.status_code in (401, 403):
                    raise A2AError("the remote agent refused the request (check its token)")
                if response.status_code >= 400:
                    raise A2AError(f"the remote agent returned HTTP {response.status_code}")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise A2AError("the remote agent's response is too large")
                    chunks.append(chunk)
    except httpx.TimeoutException as exc:
        raise A2AError("the remote agent timed out") from exc
    except httpx.HTTPError as exc:
        raise A2AError("the remote agent could not be reached") from exc
    try:
        return json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise A2AError("the remote agent returned invalid JSON") from exc


def _parse_card(data: Any, base_url: str) -> AgentCard:
    if not isinstance(data, dict):
        raise A2AError("the agent card is not a JSON object")
    protocol_version = ""
    rpc_url = ""
    interfaces = data.get("supportedInterfaces")
    if isinstance(interfaces, list):  # 1.x: the first JSON-RPC interface is used
        for interface in interfaces:
            if isinstance(interface, dict) and str(interface.get("protocolBinding", "")).upper() == "JSONRPC":
                rpc_url = str(interface.get("url") or "")
                protocol_version = str(interface.get("protocolVersion") or "1.0")
                break
    if not rpc_url and isinstance(data.get("url"), str):  # 0.x: the card's own url, JSON-RPC by default
        transport = str(data.get("preferredTransport") or "JSONRPC").upper()
        if transport == "JSONRPC":
            rpc_url = data["url"]
            protocol_version = str(data.get("protocolVersion") or "0.3")
    if not rpc_url:
        raise A2AError("the agent card names no JSON-RPC endpoint")
    skills = []
    for skill in data.get("skills") or []:
        if isinstance(skill, dict) and len(skills) < MAX_SKILLS:
            skills.append({"name": _text(skill.get("name"), 120), "description": _text(skill.get("description"), 300)})
    name = _text(data.get("name"), 120)
    if not name:
        raise A2AError("the agent card has no name")
    return AgentCard(
        name=name,
        description=_text(data.get("description"), 1000),
        version=_text(data.get("version"), 40),
        protocol_version=_text(protocol_version, 20),
        rpc_url=validate_url(rpc_url),
        skills=skills,
    )


def fetch_card(base_url: str) -> AgentCard:
    """Fetch and parse the agent card at ``base_url`` (current path first, then the legacy one)."""
    base = validate_url(base_url).rstrip("/")
    last_error: A2AError | None = None
    for path in (CARD_PATH, LEGACY_CARD_PATH):
        try:
            data = _request("GET", base + path, body=None, headers={"accept": "application/json"})
        except A2AError as exc:
            last_error = exc
            continue
        return _parse_card(data, base)
    raise A2AError(f"no agent card found ({last_error})")


def _state(task: dict[str, Any]) -> str:
    status = task.get("status") if isinstance(task.get("status"), dict) else {}
    raw = str(status.get("state") or "unknown").lower()
    return raw.removeprefix("task_state_").replace("_", "-")


def _parts_text(parts: Any) -> list[str]:
    texts = []
    for part in parts if isinstance(parts, list) else []:
        if isinstance(part, dict):
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif "data" in part and not isinstance(part.get("data"), str | bytes):
                texts.append(json.dumps(part["data"], ensure_ascii=False)[:5000])
    return texts


def _task_text(task: dict[str, Any]) -> str:
    texts: list[str] = []
    for artifact in task.get("artifacts") or []:
        if isinstance(artifact, dict):
            texts.extend(_parts_text(artifact.get("parts")))
    if not texts:
        status = task.get("status") if isinstance(task.get("status"), dict) else {}
        message = status.get("message") if isinstance(status.get("message"), dict) else {}
        texts.extend(_parts_text(message.get("parts")))
    return "\n\n".join(texts)


class A2AClient:
    """One remote agent's JSON-RPC endpoint, with the protocol revision its card declared."""

    def __init__(self, rpc_url: str, protocol_version: str, *, token: str | None = None):
        """Validate the endpoint; ``token`` is sent as a bearer token."""
        self.rpc_url = validate_url(rpc_url)
        self.v1 = _major(protocol_version) >= 1
        self.token = token
        self._next_id = 0

    def _call(self, method_v1: str, method_v0: str, params: dict[str, Any]) -> Any:
        self._next_id += 1
        headers = {"accept": "application/json", "content-type": "application/json"}
        headers["a2a-version"] = "1.0" if self.v1 else "0.3"
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method_v1 if self.v1 else method_v0, "params": params}
        payload = _request("POST", self.rpc_url, body=body, headers=headers)
        if not isinstance(payload, dict):
            raise A2AError("the remote agent returned an invalid JSON-RPC response")
        if "error" in payload:
            error = payload["error"] if isinstance(payload["error"], dict) else {}
            raise A2AError(f"the remote agent reported an error: {_text(error.get('message'), 300) or 'unknown'}")
        return payload.get("result")

    def _message(self, prompt: str) -> dict[str, Any]:
        if self.v1:
            return {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": prompt}]}
        return {
            "kind": "message",
            "messageId": str(uuid.uuid4()),
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}],
        }

    def ask(self, prompt: str, *, deadline_seconds: float = TASK_DEADLINE_SECONDS) -> AgentAnswer:
        """Send ``prompt`` and return the answer, polling a running task until ``deadline_seconds``."""
        deadline = time.monotonic() + deadline_seconds
        configuration: dict[str, Any] = {"acceptedOutputModes": _ACCEPTED_OUTPUT}
        if not self.v1:
            configuration["blocking"] = True
        result = self._call(
            "SendMessage", "message/send", {"message": self._message(prompt), "configuration": configuration}
        )
        if not isinstance(result, dict):
            raise A2AError("the remote agent returned no result")
        task = result.get("task") if isinstance(result.get("task"), dict) else None
        message = result.get("message") if isinstance(result.get("message"), dict) else None
        if task is None and message is None:  # 0.x: the result is the task or message itself
            if result.get("kind") == "task" or "status" in result:
                task = result
            else:
                message = result
        if message is not None:
            return self._answer("\n\n".join(_parts_text(message.get("parts"))), "completed")
        assert task is not None
        while _state(task) not in _TERMINAL | _INTERRUPTED:
            if time.monotonic() + POLL_INTERVAL_SECONDS > deadline:
                return self._answer(_task_text(task), f"still {_state(task)} after {deadline_seconds:g} s")
            time.sleep(POLL_INTERVAL_SECONDS)
            polled = self._call("GetTask", "tasks/get", {"id": str(task.get("id") or "")})
            if not isinstance(polled, dict):
                raise A2AError("the remote agent returned an invalid task")
            task = polled.get("task") if isinstance(polled.get("task"), dict) else polled
        return self._answer(_task_text(task), _state(task))

    @staticmethod
    def _answer(text: str, state: str) -> AgentAnswer:
        truncated = len(text) > MAX_ANSWER_CHARS
        return AgentAnswer(text=text[:MAX_ANSWER_CHARS], state=state, truncated=truncated)


__all__ = [
    "CARD_PATH",
    "LEGACY_CARD_PATH",
    "MAX_ANSWER_CHARS",
    "TASK_DEADLINE_SECONDS",
    "A2AClient",
    "A2AError",
    "AgentAnswer",
    "AgentCard",
    "fetch_card",
    "validate_url",
]
