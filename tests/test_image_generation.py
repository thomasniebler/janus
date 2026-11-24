"""Image providers, capability detection, and the image settings (JAN-253).

Every provider is exercised against its real request/response contract through
``httpx.MockTransport`` (``image_generation.HTTP_TRANSPORT``); nothing reaches
the network.
"""

import base64
import json
import struct
import zlib

import httpx
import pytest

from janus import capability_profile, models, security
from janus.services import image_generation
from janus.services.image_generation import (
    GeminiImageProvider,
    ImageChoice,
    ImageConfigError,
    ImageGenerationError,
    OpenAIImageProvider,
    OpenRouterImageProvider,
)

# db, client and auth_client fixtures are provided by conftest.py


def png(width: int = 3, height: int = 2) -> bytes:
    """A real, minimal PNG of ``width`` x ``height`` grey pixels."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def jpeg(width: int = 640, height: int = 480) -> bytes:
    """JPEG header bytes with an SOF0 frame (enough for sniffing)."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def webp_vp8x(width: int = 1024, height: int = 576) -> bytes:
    """WebP (VP8X) header bytes."""
    payload = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


class Recorder:
    """A mock transport that records requests and answers with a fixed response."""

    def __init__(self, status: int = 200, body: object | bytes = b"{}"):
        """Answer every request with ``status`` and ``body`` (JSON-encoded unless bytes)."""
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record ``request`` and answer it."""
        self.requests.append(request)
        return httpx.Response(self.status, content=self.body, headers={"content-type": "application/json"})

    @property
    def json(self) -> dict:
        """The JSON body of the last request."""
        return json.loads(self.requests[-1].content)


@pytest.fixture
def transport(monkeypatch):
    """Install a mock transport for every provider call; returns a function that sets the answer."""

    def install(status: int = 200, body: object | bytes = b"{}") -> Recorder:
        recorder = Recorder(status, body)
        monkeypatch.setattr(image_generation, "HTTP_TRANSPORT", httpx.MockTransport(recorder))
        return recorder

    return install


B64_PNG = base64.b64encode(png()).decode()


# -- choice and capability ----------------------------------------------------


def test_choice_defaults_and_rejects_providers_without_image_generation():
    """Only OpenAI, Gemini, and OpenRouter can be chosen; an empty provider is off."""
    assert image_generation.normalize_image_choice(None, None) is None
    assert image_generation.normalize_image_choice("  ", "x") is None
    assert image_generation.normalize_image_choice("OpenAI", "") == ImageChoice("openai", "gpt-image-1")
    assert image_generation.normalize_image_choice("gemini", None).model == "gemini-2.5-flash-image"
    assert image_generation.normalize_image_choice("openrouter", " my/model ").model == "my/model"
    for provider in ("ollama", "anthropic", "midjourney"):
        with pytest.raises(ImageConfigError, match="Ollama and Anthropic cannot generate images"):
            image_generation.normalize_image_choice(provider, None)


def _settings(db, **fields) -> models.Settings:
    settings = models.Settings(**fields)
    db.add(settings)
    db.commit()
    return settings


def _user(db, name: str = "image-user") -> models.User:
    user = models.User(username=name, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    return user


def test_capability_needs_a_capable_provider_and_its_stored_key(db):
    """No provider, a provider without a key, or Ollama as the chat backend: no images capability."""
    user = _user(db)
    assert "images" not in capability_profile.user_connected_capabilities(db, user)
    settings = _settings(db, llm_provider=models.LLMProvider.OLLAMA, image_provider="openai")
    assert not image_generation.is_image_generation_configured(settings)
    assert "images" not in capability_profile.user_connected_capabilities(db, user)
    settings.openai_api_key = security.security_service.encrypt("sk-test")
    db.commit()
    assert image_generation.is_image_generation_configured(settings)
    assert "images" in capability_profile.user_connected_capabilities(db, user)
    # An invalid stored provider (e.g. edited by hand) counts as off, never as a crash.
    settings.image_provider = "ollama"
    db.commit()
    assert image_generation.configured_choice(settings) is None
    assert "images" not in capability_profile.user_connected_capabilities(db, user)


def test_build_provider_reuses_the_chat_credentials(db):
    """Each provider gets the stored key and base URL of the chat provider with its name."""
    encrypt = security.security_service.encrypt
    settings = _settings(
        db,
        openai_api_key=encrypt("sk-openai"),
        openai_base_url="https://gateway.example.com/v1",
        gemini_api_key=encrypt("g-key"),
        openrouter_api_key=encrypt("or-key"),
        openrouter_base_url=None,
    )
    openai = image_generation.build_provider(settings, ImageChoice("openai", "gpt-image-1"))
    assert isinstance(openai, OpenAIImageProvider)
    assert (openai.api_key, openai.base_url) == ("sk-openai", "https://gateway.example.com/v1")
    gemini = image_generation.build_provider(settings, ImageChoice("gemini", "gemini-2.5-flash-image"))
    assert isinstance(gemini, GeminiImageProvider) and gemini.api_key == "g-key"
    router = image_generation.build_provider(settings, ImageChoice("openrouter", "google/gemini-2.5-flash-image"))
    assert isinstance(router, OpenRouterImageProvider)
    assert router.base_url == "https://openrouter.ai/api/v1"
    settings.openai_api_key = None
    with pytest.raises(ImageConfigError, match="needs a OpenAI API key"):
        image_generation.build_provider(settings, ImageChoice("openai", "gpt-image-1"))


# -- OpenAI -------------------------------------------------------------------


def test_openai_request_and_response_contract(transport):
    """POST /images/generations with model, prompt, n, size; the image is data[0].b64_json."""
    recorder = transport(body={"created": 1, "data": [{"b64_json": B64_PNG, "revised_prompt": "x"}], "usage": {}})
    provider = OpenAIImageProvider(ImageChoice("openai", "gpt-image-1"), "sk-test", "https://api.openai.com/v1/")

    assert provider.generate("a red fox", "landscape") == png()
    request = recorder.requests[0]
    assert (request.method, str(request.url)) == ("POST", "https://api.openai.com/v1/images/generations")
    assert request.headers["authorization"] == "Bearer sk-test"
    assert recorder.json == {"model": "gpt-image-1", "prompt": "a red fox", "n": 1, "size": "1536x1024"}


@pytest.mark.parametrize(
    ("model", "aspect", "expected"),
    [
        ("gpt-image-1", "square", {"size": "1024x1024"}),
        ("gpt-image-1", "portrait", {"size": "1024x1536"}),
        ("dall-e-3", "landscape", {"size": "1792x1024", "response_format": "b64_json"}),
        ("dall-e-3", "portrait", {"size": "1024x1792", "response_format": "b64_json"}),
        ("dall-e-2", "portrait", {"size": "1024x1024", "response_format": "b64_json"}),
    ],
)
def test_openai_sizes_follow_the_model_family(model, aspect, expected):
    """gpt-image models never get response_format (they reject it); DALL-E models ask for base64."""
    payload = OpenAIImageProvider(ImageChoice("openai", model), "k").request_payload("p", aspect)
    assert {key: payload[key] for key in expected} == expected
    assert ("response_format" in payload) == ("response_format" in expected)


def test_openai_errors_are_short_and_never_contain_the_key(transport):
    """A non-2xx answer names the status and the provider's message, redacting the key."""
    transport(
        status=400,
        body={"error": {"message": "Your request was rejected by the safety system. key sk-secret " + "x" * 500}},
    )
    provider = OpenAIImageProvider(ImageChoice("openai", "gpt-image-1"), "sk-secret")
    with pytest.raises(ImageGenerationError) as raised:
        provider.generate("p", "square")
    message = str(raised.value)
    assert message.startswith("OpenAI answered HTTP 400: Your request was rejected by the safety system.")
    assert "sk-secret" not in message and "[redacted]" in message
    assert len(message) < 260


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"data": []}, "returned no image"),
        ({"data": [{"url": "https://cdn.example.com/x.png"}]}, "returned no image"),
        ({"data": [{"b64_json": "not base64!"}]}, "not valid base64"),
        (b"<html>gateway</html>", "not JSON"),
    ],
)
def test_openai_answers_without_an_image_fail_cleanly(transport, body, match):
    """Missing, URL-only, broken, or non-JSON answers raise an ImageGenerationError."""
    transport(body=body)
    with pytest.raises(ImageGenerationError, match=match):
        OpenAIImageProvider(ImageChoice("openai", "gpt-image-1"), "k").generate("p", "square")


def test_network_failures_and_oversized_answers_fail_cleanly(monkeypatch):
    """A transport error and a response above the cap both raise an ImageGenerationError."""

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(image_generation, "HTTP_TRANSPORT", httpx.MockTransport(refuse))
    provider = OpenAIImageProvider(ImageChoice("openai", "gpt-image-1"), "k")
    with pytest.raises(ImageGenerationError, match=r"could not reach OpenAI \(ConnectError\)"):
        provider.generate("p", "square")
    monkeypatch.setattr(image_generation, "MAX_RESPONSE_BYTES", 10)
    monkeypatch.setattr(
        image_generation, "HTTP_TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 50))
    )
    with pytest.raises(ImageGenerationError, match="larger than 20 MB"):
        provider.generate("p", "square")


# -- Gemini and OpenRouter ----------------------------------------------------


def test_gemini_request_and_response_contract(transport):
    """Gemini: generateContent with an IMAGE modality and aspect ratio; the image is the first inlineData part."""
    recorder = transport(
        body={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Here you go"}, {"inlineData": {"mimeType": "image/png", "data": B64_PNG}}],
                    }
                }
            ]
        }
    )
    provider = GeminiImageProvider(ImageChoice("gemini", "models/gemini-2.5-flash-image"), "g-key")

    assert provider.generate("a lighthouse", "portrait") == png()
    request = recorder.requests[0]
    assert str(request.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    )
    assert request.headers["x-goog-api-key"] == "g-key"
    assert "key=" not in str(request.url)
    assert recorder.json == {
        "contents": [{"parts": [{"text": "a lighthouse"}]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}},
    }


def test_gemini_refusal_without_an_image(transport):
    """A text-only (refused) answer is an error, not an empty image."""
    transport(body={"candidates": [{"content": {"parts": [{"text": "I can't draw that."}]}}]})
    with pytest.raises(ImageGenerationError, match="Gemini returned no image"):
        GeminiImageProvider(ImageChoice("gemini", "gemini-2.5-flash-image"), "k").generate("p", "square")


def test_openrouter_request_and_response_contract(transport):
    """chat/completions with image modalities; the image is a data: URL in message.images."""
    recorder = transport(
        body={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "images": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{B64_PNG}"}},
                        ],
                    }
                }
            ]
        }
    )
    provider = OpenRouterImageProvider(ImageChoice("openrouter", "google/gemini-2.5-flash-image"), "or-key")

    assert provider.generate("a cat", "square") == png()
    request = recorder.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer or-key"
    assert recorder.json == {
        "model": "google/gemini-2.5-flash-image",
        "messages": [{"role": "user", "content": "a cat"}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": "1:1"},
    }


def test_openrouter_ignores_remote_urls_and_needs_an_image(transport):
    """Only inline data: URLs are accepted: Janus never fetches a provider-chosen URL."""
    transport(body={"choices": [{"message": {"images": [{"image_url": {"url": "https://cdn.example.com/a.png"}}]}}]})
    with pytest.raises(ImageGenerationError, match="OpenRouter returned no image"):
        OpenRouterImageProvider(ImageChoice("openrouter", "m"), "k").generate("p", "square")


# -- bytes and prompt ---------------------------------------------------------


def test_sniffing_reads_type_and_size_and_rejects_everything_else():
    """PNG, JPEG, and WebP are recognized from their bytes; SVG, HTML, and junk are refused."""
    assert image_generation.sniff_image(png(3, 2)) == image_generation.SniffedImage("image/png", "png", 3, 2)
    assert image_generation.sniff_image(jpeg(640, 480)) == image_generation.SniffedImage("image/jpeg", "jpg", 640, 480)
    assert image_generation.sniff_image(webp_vp8x(1024, 576)) == image_generation.SniffedImage(
        "image/webp", "webp", 1024, 576
    )
    assert image_generation.sniff_image(b"\xff\xd8\xff\x00garbage").width is None
    for data in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"<html><script>", b"", b"GIF89a"):
        with pytest.raises(ImageGenerationError, match="not return a PNG, JPEG, or WebP"):
            image_generation.sniff_image(data)


def test_compose_prompt_limits_and_style_hint():
    """The style hint is appended; empty or oversized prompts and styles are refused."""
    assert image_generation.compose_prompt("  a fox  ", None) == "a fox"
    assert image_generation.compose_prompt("a fox", " watercolor ") == "a fox\n\nStyle: watercolor"
    with pytest.raises(ImageConfigError, match="must not be empty"):
        image_generation.compose_prompt("   ", None)
    with pytest.raises(ImageConfigError, match="the limit is 4000"):
        image_generation.compose_prompt("x" * 4001, None)
    with pytest.raises(ImageConfigError, match="the limit is 200"):
        image_generation.compose_prompt("x", "y" * 201)


# -- settings API -------------------------------------------------------------


def _admin_client(client, db):
    db.add(
        models.User(
            username="image-admin", hashed_password=security.security_service.get_password_hash("pw"), is_admin=True
        )
    )
    db.commit()
    response = client.post("/api/auth/login", json={"username": "image-admin", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_settings_api_reads_and_writes_the_image_provider(client, db):
    """PATCH validates the provider, fills in the default model, and resets the model on a provider switch."""
    admin = _admin_client(client, db)
    initial = admin.get("/api/planner/settings").json()
    assert initial["image_provider"] is None and initial["image_model"] is None
    assert initial["image_default_models"]["openai"] == "gpt-image-1"
    assert initial["image_storage_limit_mb"] == 200

    saved = admin.patch("/api/planner/settings", json={"image_provider": "openai"}).json()
    assert (saved["image_provider"], saved["image_model"]) == ("openai", "gpt-image-1")
    saved = admin.patch("/api/planner/settings", json={"image_model": "dall-e-3"}).json()
    assert (saved["image_provider"], saved["image_model"]) == ("openai", "dall-e-3")
    saved = admin.patch("/api/planner/settings", json={"image_provider": "gemini"}).json()
    assert (saved["image_provider"], saved["image_model"]) == ("gemini", "gemini-2.5-flash-image")

    refused = admin.patch("/api/planner/settings", json={"image_provider": "ollama"})
    assert refused.status_code == 422
    assert "cannot generate images" in refused.json()["detail"]

    off = admin.patch("/api/planner/settings", json={"image_provider": None}).json()
    assert (off["image_provider"], off["image_model"]) == (None, None)
