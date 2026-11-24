"""Cloud image providers for the chat tool ``generate_image`` (JAN-253).

Image generation is off until an admin sets ``Settings.image_provider``. Each
provider reuses the stored, Fernet-encrypted API key and base URL of the chat
provider with the same name (like embeddings, ``services/embedding_service.py``),
so there is no second credential. Cloud only (owner decision 2026-09-28):
Ollama and Anthropic have no image generation and cannot be chosen.

* :func:`normalize_image_choice` / :func:`configured_choice` validate the
  provider/model pair; :func:`is_image_generation_configured` is the
  capability signal (``capability_profile``): a provider is chosen *and* its
  key is stored.
* :class:`OpenAIImageProvider`, :class:`GeminiImageProvider`, and
  :class:`OpenRouterImageProvider` speak each provider's HTTP contract through
  ``httpx`` (no SDK); :func:`build_provider` builds one from the settings row.
* :func:`sniff_image` decides the image type from its bytes (PNG, JPEG, WebP
  only) and reads its size, so nothing the provider claims is trusted.

Errors are :class:`ImageGenerationError` with a short message that is safe to
show the model and the user (never the key). See "Chat image generation" in
``docs/docs/tech-spec/background-workers-runtime.md``.
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from janus import models, security

# Shared with the chat client, which treats this host as OpenAI itself (JAN-354).
from janus.services.llm_provider_clients import DEFAULT_OPENAI_BASE_URL

PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"
PROVIDER_OPENROUTER = "openrouter"

#: Providers that can generate images, and the model used when none is set.
DEFAULT_IMAGE_MODELS: dict[str, str] = {
    PROVIDER_OPENAI: "gpt-image-1",
    PROVIDER_GEMINI: "gemini-2.5-flash-image",
    PROVIDER_OPENROUTER: "google/gemini-2.5-flash-image",
}
PROVIDER_LABELS: dict[str, str] = {
    PROVIDER_OPENAI: "OpenAI",
    PROVIDER_GEMINI: "Gemini",
    PROVIDER_OPENROUTER: "OpenRouter",
}


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

Aspect = Literal["square", "landscape", "portrait"]
ASPECTS: tuple[str, ...] = ("square", "landscape", "portrait")
_ASPECT_RATIOS: dict[str, str] = {"square": "1:1", "landscape": "16:9", "portrait": "9:16"}
_GPT_IMAGE_SIZES: dict[str, str] = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}
_DALL_E_3_SIZES: dict[str, str] = {"square": "1024x1024", "landscape": "1792x1024", "portrait": "1024x1792"}

MAX_PROMPT_CHARS = 4000
MAX_STYLE_CHARS = 200
REQUEST_TIMEOUT_SECONDS = 120.0
#: Largest provider response read (a base64 image of a few MB fits easily).
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_MAX_ERROR_CHARS = 200

#: Overridable in tests (``httpx.MockTransport``); ``None`` = the real network.
HTTP_TRANSPORT: httpx.BaseTransport | None = None


class ImageConfigError(ValueError):
    """The chosen image provider/model is not usable; the message says why."""


class ImageGenerationError(RuntimeError):
    """A provider call failed; the message is short and safe to show."""


@dataclass(frozen=True)
class ImageChoice:
    """The configured image provider and model."""

    provider: str
    model: str

    @property
    def label(self) -> str:
        """``<Provider> <model>``, for captions and logs."""
        return f"{PROVIDER_LABELS[self.provider]} {self.model}"


@dataclass(frozen=True)
class SniffedImage:
    """An image's verified type and (when readable) its pixel size."""

    mime_type: str
    extension: str
    width: int | None
    height: int | None


def normalize_image_choice(provider: str | None, model: str | None) -> ImageChoice | None:
    """Validate a provider/model pair; an empty provider turns image generation off.

    A provider without a model gets that provider's default model. Raises
    :class:`ImageConfigError` for a provider that cannot generate images.
    """
    cleaned_provider = (provider or "").strip().lower()
    if not cleaned_provider:
        return None
    if cleaned_provider not in DEFAULT_IMAGE_MODELS:
        supported = ", ".join(sorted(DEFAULT_IMAGE_MODELS))
        raise ImageConfigError(
            f"image_provider must be one of: {supported} (or empty to turn image generation off); "
            "Ollama and Anthropic cannot generate images"
        )
    cleaned_model = (model or "").strip() or DEFAULT_IMAGE_MODELS[cleaned_provider]
    return ImageChoice(provider=cleaned_provider, model=cleaned_model)


def _text(settings: models.Settings, field: str) -> str | None:
    """One string column of the settings row (``None`` when unset)."""
    value = getattr(settings, field, None)
    return value if isinstance(value, str) and value else None


def configured_choice(settings: models.Settings | None) -> ImageChoice | None:
    """Return the stored image choice, or ``None`` when it is off or invalid."""
    if settings is None:
        return None
    try:
        return normalize_image_choice(_text(settings, "image_provider"), _text(settings, "image_model"))
    except ImageConfigError:
        return None


def _stored_key(settings: models.Settings, provider: str) -> str | None:
    encrypted = _text(settings, f"{provider}_api_key")
    return security.security_service.decrypt(encrypted) if encrypted else None


def is_image_generation_configured(settings: models.Settings | None) -> bool:
    """Return whether a capable provider is chosen and its API key is stored."""
    choice = configured_choice(settings)
    return choice is not None and settings is not None and _text(settings, f"{choice.provider}_api_key") is not None


def sniff_image(data: bytes) -> SniffedImage:
    """Return the type and size of PNG, JPEG, or WebP ``data``; raise for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return SniffedImage("image/png", "png", width, height)
    if data.startswith(b"\xff\xd8\xff"):
        return SniffedImage("image/jpeg", "jpg", *_jpeg_size(data))
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return SniffedImage("image/webp", "webp", *_webp_size(data))
    raise ImageGenerationError("the provider did not return a PNG, JPEG, or WebP image")


def _jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    index = 2
    while index + 9 <= len(data):
        if data[index] != 0xFF:
            return None, None
        marker = data[index + 1]
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        index += 2 + length
    return None, None


def _webp_size(data: bytes) -> tuple[int | None, int | None]:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    return None, None


def _decode_base64(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise ImageGenerationError("the provider returned no image")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ImageGenerationError("the provider returned an image that is not valid base64") from error


def _error_detail(body: bytes, secret: str) -> str:
    try:
        parsed = json.loads(body)
    except ValueError:
        text = body.decode("utf-8", "replace")
    else:
        error = parsed[0] if isinstance(parsed, list) and parsed else parsed
        detail = error.get("error", error) if isinstance(error, dict) else error
        text = str(detail.get("message", detail) if isinstance(detail, dict) else detail)
    text = " ".join(text.split()).replace(secret, "[redacted]") if secret else " ".join(text.split())
    return text[:_MAX_ERROR_CHARS]


def _post_json(label: str, url: str, headers: dict[str, str], payload: dict[str, Any], secret: str) -> Any:
    """POST ``payload`` and return the parsed JSON answer, reading at most ``MAX_RESPONSE_BYTES``."""
    try:
        with (
            httpx.Client(transport=HTTP_TRANSPORT, timeout=REQUEST_TIMEOUT_SECONDS) as client,
            client.stream("POST", url, headers=headers, json=payload) as response,
        ):
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ImageGenerationError(f"{label} sent a response larger than 20 MB")
            status = response.status_code
    except httpx.HTTPError as error:
        raise ImageGenerationError(f"could not reach {label} ({error.__class__.__name__})") from error
    if status >= 400:
        detail = _error_detail(bytes(body), secret)
        raise ImageGenerationError(f"{label} answered HTTP {status}" + (f": {detail}" if detail else ""))
    try:
        return json.loads(body)
    except ValueError as error:
        raise ImageGenerationError(f"{label} sent a response that is not JSON") from error


class ImageProvider(Protocol):
    """One configured image backend."""

    @property
    def choice(self) -> ImageChoice:
        """The provider and model this backend calls."""
        ...

    def generate(self, prompt: str, aspect: str) -> bytes:
        """Return the raw image bytes for ``prompt``."""
        ...


@dataclass(frozen=True)
class OpenAIImageProvider:
    """OpenAI Images API (``POST /images/generations``)."""

    choice: ImageChoice
    api_key: str
    base_url: str = DEFAULT_OPENAI_BASE_URL

    def request_payload(self, prompt: str, aspect: str) -> dict[str, Any]:
        """The JSON body for ``prompt`` (sizes depend on the model family)."""
        model = self.choice.model
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        if model.startswith("dall-e-2"):
            payload["size"] = "1024x1024"
        elif model.startswith("dall-e"):
            payload["size"] = _DALL_E_3_SIZES[aspect]
        else:
            payload["size"] = _GPT_IMAGE_SIZES[aspect]
        if model.startswith("dall-e"):
            # gpt-image models always answer base64 and reject this field.
            payload["response_format"] = "b64_json"
        return payload

    def generate(self, prompt: str, aspect: str) -> bytes:
        """Generate one image."""
        answer = _post_json(
            "OpenAI",
            f"{self.base_url.rstrip('/')}/images/generations",
            {"Authorization": f"Bearer {self.api_key}"},
            self.request_payload(prompt, aspect),
            self.api_key,
        )
        data = answer.get("data") if isinstance(answer, dict) else None
        first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        return _decode_base64(first.get("b64_json"))


@dataclass(frozen=True)
class GeminiImageProvider:
    """Gemini image models (``models/<model>:generateContent`` with an IMAGE modality)."""

    choice: ImageChoice
    api_key: str

    def request_payload(self, prompt: str, aspect: str) -> dict[str, Any]:
        """The JSON body for ``prompt``."""
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": _ASPECT_RATIOS[aspect]},
            },
        }

    def generate(self, prompt: str, aspect: str) -> bytes:
        """Generate one image."""
        model = self.choice.model.removeprefix("models/")
        answer = _post_json(
            "Gemini",
            f"{GEMINI_BASE_URL}/models/{model}:generateContent",
            {"x-goog-api-key": self.api_key},
            self.request_payload(prompt, aspect),
            self.api_key,
        )
        candidates = answer.get("candidates") if isinstance(answer, dict) else None
        content = candidates[0].get("content", {}) if isinstance(candidates, list) and candidates else {}
        for part in content.get("parts") or ():
            inline = part.get("inlineData") or part.get("inline_data") if isinstance(part, dict) else None
            if isinstance(inline, dict) and inline.get("data"):
                return _decode_base64(inline["data"])
        raise ImageGenerationError("Gemini returned no image (the prompt may have been refused)")


@dataclass(frozen=True)
class OpenRouterImageProvider:
    """OpenRouter image-capable models (``/chat/completions`` with ``modalities``)."""

    choice: ImageChoice
    api_key: str
    base_url: str = DEFAULT_OPENROUTER_BASE_URL

    def request_payload(self, prompt: str, aspect: str) -> dict[str, Any]:
        """The JSON body for ``prompt``."""
        return {
            "model": self.choice.model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
            "image_config": {"aspect_ratio": _ASPECT_RATIOS[aspect]},
        }

    def generate(self, prompt: str, aspect: str) -> bytes:
        """Generate one image."""
        answer = _post_json(
            "OpenRouter",
            f"{self.base_url.rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            self.request_payload(prompt, aspect),
            self.api_key,
        )
        choices = answer.get("choices") if isinstance(answer, dict) else None
        message = choices[0].get("message", {}) if isinstance(choices, list) and choices else {}
        for image in message.get("images") or ():
            url = (image.get("image_url") or {}).get("url") if isinstance(image, dict) else None
            if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
                return _decode_base64(url.split(";base64,", 1)[1])
        raise ImageGenerationError("OpenRouter returned no image (is the model image-capable?)")


def build_provider(settings: models.Settings, choice: ImageChoice) -> ImageProvider:
    """Build the provider for ``choice`` from the stored credentials."""
    api_key = _stored_key(settings, choice.provider)
    if not api_key:
        label = PROVIDER_LABELS[choice.provider]
        raise ImageConfigError(f"{label} image generation needs a {label} API key in the AI settings")
    if choice.provider == PROVIDER_OPENAI:
        return OpenAIImageProvider(choice, api_key, _text(settings, "openai_base_url") or DEFAULT_OPENAI_BASE_URL)
    if choice.provider == PROVIDER_GEMINI:
        return GeminiImageProvider(choice, api_key)
    base_url = _text(settings, "openrouter_base_url") or DEFAULT_OPENROUTER_BASE_URL
    return OpenRouterImageProvider(choice, api_key, base_url)


def compose_prompt(prompt: str, style: str | None) -> str:
    """Validate the model's prompt and style hint and join them into the text sent."""
    cleaned = prompt.strip()
    if not cleaned:
        raise ImageConfigError("prompt must not be empty")
    if len(cleaned) > MAX_PROMPT_CHARS:
        raise ImageConfigError(f"prompt is {len(cleaned)} characters; the limit is {MAX_PROMPT_CHARS}")
    hint = (style or "").strip()
    if len(hint) > MAX_STYLE_CHARS:
        raise ImageConfigError(f"style is {len(hint)} characters; the limit is {MAX_STYLE_CHARS}")
    return f"{cleaned}\n\nStyle: {hint}" if hint else cleaned


__all__ = [
    "ASPECTS",
    "DEFAULT_IMAGE_MODELS",
    "GeminiImageProvider",
    "ImageChoice",
    "ImageConfigError",
    "ImageGenerationError",
    "ImageProvider",
    "OpenAIImageProvider",
    "OpenRouterImageProvider",
    "SniffedImage",
    "build_provider",
    "compose_prompt",
    "configured_choice",
    "is_image_generation_configured",
    "normalize_image_choice",
    "sniff_image",
]
