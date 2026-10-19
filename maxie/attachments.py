"""Screenshots, pasted images and dropped files on a turn, as Claude API content blocks.

Images (from the notch app's screenshot or a paste) become `image` blocks; files must sit in an allowed
folder: text files are read (capped) and wrapped as untrusted data, image files become image blocks.
Nothing binary is stored: the conversation keeps only a "[screenshot]" / "[file: name]" marker."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from .hands.mac import in_allowed
from .safety import wrap_untrusted

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
TEXT_MAX_CHARS = 20_000
IMAGE_MAX_BYTES = 5_000_000  # the API's limit per image


@dataclass
class Attachments:
    blocks: list[dict] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)   # what the conversation log keeps
    problem: str | None = None                          # a refusal to show instead of calling the model

    @property
    def has_image(self) -> bool:
        return any(b["type"] == "image" for b in self.blocks)


def image_block(data_b64: str, media_type: str = "image/png") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data_b64}}


def gather(images: list[str] | None, files: list[str] | None, allowed_folders: list[str]) -> Attachments:
    out = Attachments()
    for raw in images or []:
        media, data = "image/png", raw
        if raw.startswith("data:"):  # data:image/jpeg;base64,....
            head, _, data = raw.partition(",")
            media = head[5:].split(";")[0] or media
        if len(data) * 3 // 4 > IMAGE_MAX_BYTES:
            out.problem = "That image is too big (over 5 MB)."
            return out
        out.blocks.append(image_block(data, media))
        out.markers.append("[screenshot]")
    for raw in files or []:
        path = Path(raw).expanduser()
        if not in_allowed(str(path), allowed_folders):
            out.problem = f"{path.name} isn't in an allowed folder (Settings), so I didn't read it."
            return out
        if not path.is_file():
            out.problem = f"I can't find {path.name}."
            return out
        media = IMAGE_TYPES.get(path.suffix.lower())
        if media:
            out.blocks.append(image_block(base64.b64encode(path.read_bytes()).decode(), media))
        else:
            try:
                text = path.read_text(errors="replace")[:TEXT_MAX_CHARS]
            except OSError as e:
                out.problem = f"I couldn't read {path.name}: {e.strerror or e}."
                return out
            out.blocks.append({"type": "text", "text": wrap_untrusted(f"file {path.name}", text)})
        out.markers.append(f"[file: {path.name}]")
    return out
