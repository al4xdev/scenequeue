from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import fields
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from . import core as cfg
from .core import logger
from .enums import GenerationConfig


# =====================================================================
# 1. ComfyUI API Client
# =====================================================================
class ComfyClient:
    def __init__(self, server_url: str = "http://127.0.0.1:8188") -> None:
        self.server_url: str = server_url.rstrip("/")

    @classmethod
    def setup(cls, server_url: str = "http://127.0.0.1:8188") -> ComfyClient:
        return cls(server_url)

    async def queue_prompt(self, workflow_api: dict[str, Any]) -> str:
        url = f"{self.server_url}/prompt"
        max_retries = 3
        backoff = 1.0
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        url, json={"prompt": workflow_api, "client_id": cfg.CLIENT_ID}
                    )
                    resp.raise_for_status()
                    return resp.json().get("prompt_id", "")
            except (httpx.HTTPError, httpx.ConnectError) as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to queue prompt after {max_retries} attempts: {e}")
                    raise
                logger.warning(
                    f"Failed to queue prompt (attempt {attempt + 1}/{max_retries}), retrying in {backoff}s... Error: {e}"
                )
                await asyncio.sleep(backoff)
                backoff *= 2
        return ""


# =====================================================================
# 2. Image Helpers
# =====================================================================
def generate_thumbnail(img_path: Path, thumb_path: Path) -> None:
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.open(img_path)
        img.thumbnail((400, 400))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        thumb_path = thumb_path.with_suffix(".jpg")
        img.save(thumb_path, "JPEG", quality=80)
    except Exception as e:
        logger.error(f"Failed to generate thumbnail for {img_path}: {e}")


# =====================================================================
# 3. Prompt Resolver
# =====================================================================
SEGMENT_SEPARATOR = "\n\n---\n\n"


def wrap_segment(text: str) -> str:
    return f"positive:{text.strip()}\nnegative:"


def join_segments(segments: list[str]) -> str:
    return SEGMENT_SEPARATOR.join(wrap_segment(s) for s in segments)


class PromptResolver:
    def __init__(self, config: GenerationConfig) -> None:
        self.replacements: dict[str, str] = {}
        for field in fields(config):
            val = getattr(config, field.name)
            if hasattr(val, "value"):
                self.replacements[f"{{{field.name}}}"] = val.value
            else:
                self.replacements[f"{{{field.name}}}"] = str(val)

    @classmethod
    def setup(cls, config: GenerationConfig) -> PromptResolver:
        return cls(config)

    def resolve_text(self, text: str) -> str:
        resolved = text
        style_val = self.replacements.get("{style}", "")
        if not style_val:
            resolved = re.sub(r"\(\{style\}:1\.2\),\s*", "", resolved, flags=re.IGNORECASE)
            resolved = re.sub(r"\(\{style\}:1\.2\)", "", resolved, flags=re.IGNORECASE)
        for placeholder, value in self.replacements.items():
            resolved = re.sub(re.escape(placeholder), value, resolved, flags=re.IGNORECASE)
        return resolved

    def resolve_file(self, src_path: str | Path, dest_path: str | Path) -> PromptResolver:
        src = Path(src_path)
        dest = Path(dest_path)
        with src.open("r", encoding="utf-8") as f:
            content = f.read()
        resolved_content = self.resolve_text(content)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as f:
            f.write(resolved_content)
        return self


# =====================================================================
# 4. Workflow JSON Builders
# =====================================================================
def _load_template(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_prompt_file(prompt_text: str) -> str:
    """Write prompt to ComfyUI input dir so LoadPromptsFromFile can read it."""
    dest = cfg.COMFY_ROOT / "input" / "scenequeue" / "prompt.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(prompt_text, encoding="utf-8")
    return "scenequeue/prompt.txt"


def _update_common_nodes(wf: dict, output_subdir: str) -> None:
    for node_id, node_info in wf.items():
        c_type = node_info.get("class_type", "")
        if c_type in ("EmptySD3LatentImage", "EmptyLatentImage"):
            node_info["inputs"]["width"] = cfg.WIDTH
            node_info["inputs"]["height"] = cfg.HEIGHT
        elif c_type in ("APZmedia Fast image save", "easy imageSave"):
            node_info["inputs"]["output_path"] = str(cfg.OUTPUT_DIR / output_subdir)
        elif c_type in ("SaveImage", "SaveImage //Inspire"):
            prefix = node_info["inputs"].get("filename_prefix", "ComfyUI")
            node_info["inputs"]["filename_prefix"] = f"{output_subdir}/{prefix}"


def build_batch(prompt_text: str, chunk_number: str, session_id: str) -> dict:
    logger.info(f"Building batch for session {session_id}, chunk {chunk_number}")
    wf = _load_template(cfg.WORKFLOW_PATH)
    _update_common_nodes(wf, f"{session_id}/{chunk_number}")

    # Inject dynamic Checkpoint and LoRAs configuration
    if cfg.CHECKPOINT:
        for node_id, node_info in wf.items():
            if node_info.get("class_type") in ("CheckpointLoaderKJ", "CheckpointLoaderSimple"):
                node_info["inputs"]["ckpt_name"] = cfg.CHECKPOINT
                logger.info(f"Injected checkpoint {cfg.CHECKPOINT} into node {node_id}")
                break

    # Find all LoraLoader nodes sorted by ID
    lora_nodes = []
    for node_id, node_info in sorted(
        wf.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999
    ):
        if node_info.get("class_type") == "LoraLoader":
            lora_nodes.append((node_id, node_info))

    # Apply LoRA configurations in order
    for idx, (node_id, node_info) in enumerate(lora_nodes):
        if cfg.LORAS and idx < len(cfg.LORAS):
            lora_cfg = cfg.LORAS[idx]
            name = lora_cfg.get("name", "")
            if name and name != "None":
                node_info["inputs"]["lora_name"] = name
                node_info["inputs"]["strength_model"] = float(lora_cfg.get("strength_model", 1.0))
                node_info["inputs"]["strength_clip"] = float(lora_cfg.get("strength_clip", 1.0))
                logger.info(f"Injected LoRA {name} into node {node_id}")
            else:
                node_info["inputs"]["strength_model"] = 0.0
                node_info["inputs"]["strength_clip"] = 0.0
                logger.info(f"Disabled LoRA node {node_id} (name was None/empty)")
        elif cfg.LORAS is not None:
            node_info["inputs"]["strength_model"] = 0.0
            node_info["inputs"]["strength_clip"] = 0.0
            logger.info(f"Disabled LoRA node {node_id} (not configured)")

    if cfg.TARGET_NODE_ID in wf:
        ni = wf[cfg.TARGET_NODE_ID]
        if ni.get("class_type") == "LoadPromptsFromFile //Inspire":
            prompt_file = _write_prompt_file(prompt_text)
            ni["inputs"]["prompt_file"] = prompt_file
            ni["inputs"]["text_data_opt"] = prompt_text
        else:
            ni["inputs"][cfg.TARGET_INPUT_KEY] = str(prompt_text)
    else:
        raise ValueError(f"Prompt node {cfg.TARGET_NODE_ID!r} is not present in the workflow")
    return wf


def build_upscale(session_id: str, chunk_number: str, image_index: int | None = None) -> dict:
    wf = _load_template(cfg.UPSCALE_WORKFLOW_PATH)
    source_dir = cfg.OUTPUT_DIR / session_id / chunk_number
    source_images = (
        sorted(p for p in source_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".webp"})
        if source_dir.exists()
        else []
    )
    if not source_images:
        raise FileNotFoundError(f"No source image found in {source_dir}")
    source_index = image_index or 0
    if source_index >= len(source_images):
        raise IndexError(f"Image index {source_index} is outside the source batch")
    source_image = source_images[source_index]
    input_relative = (
        Path("scenequeue")
        / f"{session_id}-{chunk_number}-{source_index}{source_image.suffix.lower()}"
    )
    input_path = cfg.COMFY_ROOT / "input" / input_relative
    input_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_image, input_path)

    for node_id, node_info in wf.items():
        c_type = node_info.get("class_type", "")
        if c_type == "LoadImage":
            node_info["inputs"]["image"] = input_relative.as_posix()

    output_subdir = f"{session_id}/upscaled/{chunk_number}"
    if image_index is not None:
        output_subdir = f"{session_id}/upscaled/{chunk_number}_{image_index}"
    _update_common_nodes(wf, output_subdir)
    return wf
