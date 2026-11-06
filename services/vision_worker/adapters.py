"""Replaceable vision adapters. Output is ImageObservation: model-generated, never verified."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from shared.schemas.vision import ImageObservation
from shared.utilities.anthropic_client import AnthropicClient, ProviderError, ProviderUnavailable
from shared.utilities.jsonutil import extract_json


class VisionUnavailable(Exception):
    pass


class VisionError(Exception):
    pass


class VisionAdapter(Protocol):
    name: str
    provenance: str  # "MODEL_GENERATED" | "SIMULATED"

    def analyze(self, image_uri: str, uploaded_by: str) -> ImageObservation: ...


class SimulatedVisionAdapter:
    """Offline demo adapter: returns pre-written annotations for synthetic images. It does NOT look at pixels."""
    name, provenance = "simulated", "SIMULATED"

    def __init__(self, annotations: dict[str, dict]) -> None:
        self.annotations = annotations

    def analyze(self, image_uri: str, uploaded_by: str) -> ImageObservation:
        a = self.annotations.get(image_uri)
        if a is None:
            raise VisionUnavailable("no simulated annotation for this image")
        return ImageObservation(visible_damage=a["visible_damage"], damage_description=a.get("description"),
                                packaging_condition=a["packaging"], product_category_seen=a["category"],
                                model_confidence="MEDIUM")


SYSTEM = ("You describe photographs of returned e-commerce parcels. Report ONLY what is visibly present. "
          "Do not read or guess serial numbers, do not judge authenticity or whether anyone is lying. "
          "If unsure, answer 'unclear'. Reply with a single JSON object and nothing else.")
PROMPT = ('Return JSON with keys: visible_damage ("yes"|"no"|"unclear"), damage_description (short string or null), '
          'packaging_condition ("intact"|"damaged"|"opened"|"unclear"), product_category_seen (short lowercase noun, '
          '"none" if the box looks empty, or null if unclear), model_confidence ("LOW"|"MEDIUM"|"HIGH").')
MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


class AnthropicVisionAdapter:
    """Calls a multimodal model via the Anthropic API. Credentials come from the environment only.
    Local files only (restricted to image_root); remote URLs are never fetched (SSRF)."""
    provenance = "MODEL_GENERATED"

    def __init__(self, client: AnthropicClient | None, image_root: str | Path = "data/images") -> None:
        self.client, self.root = client, Path(image_root).resolve()
        self.name = f"anthropic:{client.model}" if client else "anthropic:unconfigured"

    @classmethod
    def from_env(cls, **kw) -> "AnthropicVisionAdapter":
        return cls(AnthropicClient.from_env("VISION_MODEL", **kw))

    def _load(self, uri: str) -> tuple[str, str]:
        if "://" in uri and not uri.startswith("file://"):
            raise VisionUnavailable("remote image URIs are not fetched")
        path = (self.root / uri.removeprefix("file://")).resolve() if not uri.startswith("/") else Path(uri).resolve()
        if self.root not in path.parents:
            raise VisionUnavailable("image path outside the allowed image directory")
        if path.suffix.lower() not in MEDIA or not path.is_file():
            raise VisionUnavailable("image file not found or unsupported type")
        return MEDIA[path.suffix.lower()], base64.b64encode(path.read_bytes()).decode()

    def analyze(self, image_uri: str, uploaded_by: str) -> ImageObservation:
        if self.client is None:
            raise VisionUnavailable("ANTHROPIC_API_KEY not set")
        media, data = self._load(image_uri)
        content = [{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                   {"type": "text", "text": PROMPT}]
        try:
            text = self.client.complete(SYSTEM, content, max_tokens=400)
        except ProviderUnavailable as exc:
            raise VisionUnavailable(str(exc)) from None
        except ProviderError as exc:
            raise VisionError(str(exc)) from None
        try:
            return ImageObservation.model_validate(extract_json(text))
        except (ValueError, ValidationError):
            raise VisionError("model output was not valid structured JSON") from None
