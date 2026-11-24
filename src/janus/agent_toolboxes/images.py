"""Image generation in chat with a cloud image model (JAN-253).

``generate_image`` sends the model's prompt to the configured provider
(``services/image_generation.py``), stores the picture for its owner
(``services/image_store.py``), and adds an ``image`` block pointing at
``/api/images/<id>`` to the turn's
:class:`~janus.services.ui_blocks.UIBlockTurn`, so ``websocket/chat.py``
streams it right after the call like a ``render_ui`` block. The model does not
need a second tool call to show it.

Chat only by default: the registry entry is in ``TASK_MODE_ALLOWED_TOOL_IDS["chat"]``
and ``tool_policy.LOCKED_UNATTENDED``. The one exception is the owner's
*Unattended: Allow everything* mode (JAN-359), which lifts that lock: an
unattended run then stores the picture without a conversation and answers its
``/api/images/<id>`` URL instead of showing a block. The entry needs the
``images`` capability (a capable provider with a stored key; see
``capability_profile``). ``generate_image`` is in
``runtime_adapter.INJECTION_ESCALATED_TOOL_NAMES``; the tool checks the turn
trace again itself. The prompt is never logged, only its length.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session, sessionmaker

from janus import event_logger, models
from janus.agent_toolboxes.shared import ToolContext
from janus.services import image_generation, image_store, ui_blocks
from janus.services.ui_blocks import UIBlockTurn
from janus.tool_registry import ToolBuildContext, ToolRegistryEntry

IMAGES_TOOL_ID = "images"
GENERATE_IMAGE_TOOL_NAME = "generate_image"
_LOG_SOURCE = "ImageGeneration"
_MAX_ALT_CHARS = 300


class ImageToolFailed(RuntimeError):
    """No image was made; the message says why (chat's tool error guard shows it)."""


_DESCRIPTION = (
    "Generate a picture with the configured cloud image model and show it to the user inline in this reply. "
    "The prompt (and style hint) is sent to an external provider (OpenAI, Gemini, or OpenRouter) and each call "
    "costs money, so only call it when the user asked for an image, once per image, with a clear, "
    "self-contained description. Arguments: prompt (what to draw, at most "
    f"{image_generation.MAX_PROMPT_CHARS} characters; never include private data the user did not ask to send), "
    "aspect ('square', 'landscape', or 'portrait'; default square), style (optional short hint such as "
    "'watercolor' or 'flat vector icon'). The image is shown automatically: do not call render_ui for it and do "
    "not describe it at length afterwards. If the answer is an error, tell the user why instead of retrying."
)


_UNATTENDED_DESCRIPTION = (
    "Generate a picture with the configured cloud image model and store it for the user. The prompt is sent to an "
    "external provider and each call costs money, so only call it when the task asks for an image. Arguments: "
    f"prompt (at most {image_generation.MAX_PROMPT_CHARS} characters), aspect ('square', 'landscape', or "
    "'portrait'), style (optional short hint). Answers the stored image's URL; mention it in your result."
)


@contextmanager
def _session(context: ToolContext) -> Iterator[Session]:
    # The chat's own session may hold an open transaction on another thread;
    # writes go through a short-lived one, like other chat tool writes (JAN-189).
    db = sessionmaker(bind=context.db.get_bind(), autocommit=False, autoflush=False)()
    try:
        yield db
    finally:
        db.close()


def _log(context: ToolContext, event_type: models.EventType, message: str, details: dict[str, object]) -> None:
    event_logger.log(event_type, _LOG_SOURCE, message, {"user_id": context.user.id, **details}, user_id=context.user.id)


def _injection_refusal() -> str:
    # The runtime adapter's escalation shape (runtime_adapter._traced), so the call ends ``blocked``.
    return (
        f"BLOCKED: {GENERATE_IMAGE_TOOL_NAME} was not run because content read earlier looked like a prompt "
        "injection (instructions hidden in mail, web, or other outside content). Nothing was sent to the image "
        "provider. Tell the user; they can ask for the image again in a new message. "
        "See the docs: tools.md#how-permissions-work."
    )


def _block_for(image_id: str, prompt: str, choice: image_generation.ImageChoice) -> dict[str, Any]:
    alt = prompt if len(prompt) <= _MAX_ALT_CHARS else prompt[: _MAX_ALT_CHARS - 1] + "…"
    caption = f"Generated with {choice.label}"
    return ui_blocks.validate_block(
        {"type": "image", "alt": alt, "url": image_store.image_url(image_id), "caption": caption}
    )


def _configured(context: ToolContext) -> tuple[models.Settings, image_generation.ImageChoice]:
    with _session(context) as db:
        settings = db.query(models.Settings).first()
        choice = image_generation.configured_choice(settings)
        if settings is None or choice is None:
            raise ImageToolFailed(
                "no image provider is configured; an administrator can choose one under Settings -> AI & System "
                "-> Image generation"
            )
        db.expunge(settings)
        return settings, choice


def _ensure_capacity(context: ToolContext, turn: UIBlockTurn | None) -> None:
    if turn is not None and len(turn.blocks()) >= ui_blocks.MAX_UI_BLOCKS_PER_MESSAGE:
        raise ImageToolFailed(f"a reply can show at most {ui_blocks.MAX_UI_BLOCKS_PER_MESSAGE} blocks")
    with _session(context) as db:
        image_store.ensure_capacity(db, context.user.id)


def build_image_tools(context: ToolContext, *, unattended: bool = False) -> list[BaseTool]:
    """Build the ``generate_image`` tool for one chat turn, or (``unattended``) for one unattended step."""

    @tool(GENERATE_IMAGE_TOOL_NAME, description=_UNATTENDED_DESCRIPTION if unattended else _DESCRIPTION)
    def generate_image(
        prompt: str, aspect: Literal["square", "landscape", "portrait"] = "square", style: str | None = None
    ) -> str:
        turn = None if unattended else context.ui_blocks
        conversation_id = context.conversation_id
        if not unattended:
            if not isinstance(turn, UIBlockTurn) or not turn.available:
                raise ImageToolFailed("this client cannot show images; nothing was generated. Answer in text instead")
            if conversation_id is None:
                raise ImageToolFailed("images can only be generated inside a saved chat conversation")
        settings, choice = _configured(context)
        audit: dict[str, object] = {"provider": choice.provider, "model": choice.model, "prompt_chars": len(prompt)}
        trace = context.turn_trace
        if trace is not None and trace.injection_suspected():
            _log(context, models.EventType.WARNING, "Refused image generation after a suspected injection", audit)
            return _injection_refusal()
        try:
            full_prompt = image_generation.compose_prompt(prompt, style)
            _ensure_capacity(context, turn)
            # The network call runs with no database session open.
            data = image_generation.build_provider(settings, choice).generate(full_prompt, aspect)
            sniffed = image_generation.sniff_image(data)
        except (image_generation.ImageConfigError, image_generation.ImageGenerationError) as error:
            _log(context, models.EventType.WARNING, "Image generation failed", {**audit, "reason": str(error)})
            raise ImageToolFailed(str(error)) from error
        except image_store.ImageStorageFull as error:
            _log(context, models.EventType.WARNING, "Image generation refused: storage full", audit)
            raise ImageToolFailed(str(error)) from error
        try:
            with _session(context) as db:
                image = image_store.store_image(
                    db,
                    user_id=context.user.id,
                    conversation_id=conversation_id,
                    prompt=full_prompt,
                    provider=choice.provider,
                    model=choice.model,
                    data=data,
                    sniffed=sniffed,
                )
                image_id, size_bytes, mime_type = image.id, image.size_bytes, image.mime_type
        except image_store.ImageStorageFull as error:
            # Re-checked with this image's size under the user's lock (a concurrent turn may have filled it).
            _log(context, models.EventType.WARNING, "Image generation refused: storage full", audit)
            raise ImageToolFailed(str(error)) from error
        if turn is None:
            _log(
                context,
                models.EventType.INFO,
                "Generated an image in an unattended run",
                {**audit, "image_id": image_id, "size_bytes": size_bytes, "mime_type": mime_type},
            )
            return f"Generated and stored image {image_id}: {image_store.image_url(image_id)}"
        block = _block_for(image_id, prompt.strip(), choice)
        turn.add(block)
        _log(
            context,
            models.EventType.INFO,
            "Generated an image",
            {**audit, "image_id": image_id, "size_bytes": size_bytes, "mime_type": mime_type},
        )
        return (
            f"Generated and shown to the user: image {image_id} (block {block['id']}, "
            f"{image_store.image_url(image_id)}). Do not call render_ui for it again and do not describe it at "
            "length; add only what the picture does not say."
        )

    return [generate_image]


def _enabled(context: ToolBuildContext) -> bool:
    # Chat, or an unattended step whose owner chose "Allow everything" (JAN-359).
    return context.task_mode == "chat" or context.unattended_allow_all


IMAGES_ENTRY = ToolRegistryEntry(
    id=IMAGES_TOOL_ID,
    display_name="Image generation",
    description=(
        "Generate pictures with the configured cloud image model (OpenAI, Gemini, or OpenRouter) and show them "
        "in chat; the prompt is sent to that provider and each image costs money."
    ),
    required_capabilities=("images",),
    # Sends the prompt off-host, spends money, and stores a file for the user.
    operation="write",
    risk_level="medium",
    confirmation="recommended",
    docs_ref="tools.md#image-generation",
    builder=lambda context: build_image_tools(context.tool_context, unattended=context.task_mode != "chat"),
    # Locked for unattended runs (tool_policy.LOCKED_UNATTENDED) unless the owner chose "Allow everything".
    enabled_when=_enabled,
)

__all__ = ["GENERATE_IMAGE_TOOL_NAME", "IMAGES_ENTRY", "IMAGES_TOOL_ID", "ImageToolFailed", "build_image_tools"]
