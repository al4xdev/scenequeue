from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

import src.core as cfg
from src.core import (
    DATABASES_DIR,
    FRONTEND_PATH,
    IMAGES_DIR,
    THUMBS_DIR,
    delete_session_record,
    get_active_db_name,
    get_all_segments,
    get_db_lookup,
    get_session,
    load_db,
    load_state,
    logger,
    save_db,
    save_state,
    set_active_db_name,
    state_lock,
    upsert_session,
)
from src.enums import Appearance, GenerationConfig, Pose, Scene, Style, Subject, Wardrobe
from src.workflows import (
    ComfyClient,
    ComfyQueueError,
    PromptResolver,
    build_batch,
    build_upscale,
    generate_thumbnail,
    join_segments,
    wrap_segment,
)

router = APIRouter()
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RECOMMENDED_CHECKPOINT = ""
RECOMMENDED_LORA = ""


def _validate_database_name(name: str) -> str:
    clean = name.strip()
    if not DATABASE_NAME_PATTERN.fullmatch(clean):
        raise HTTPException(
            status_code=400,
            detail="Database names may contain only letters, numbers, dashes, and underscores.",
        )
    return clean


def _update_db_segment(
    db: dict,
    segment_index: int,
    new_text: str,
    history_limit: int = 5,
    db_name: str | None = None,
    db_type: str = "prompts",
) -> bool:
    segs = db.get("segments", [])
    if segment_index < 0 or segment_index >= len(segs):
        return False

    now = datetime.now(timezone.utc).isoformat()
    segs_sorted = sorted(segs, key=lambda s: s.get("index", 0))
    seg = segs_sorted[segment_index]
    old_text = seg.get("text", "").strip()

    new_text_stripped = new_text.strip()
    if new_text_stripped.endswith("---"):
        new_text_stripped = new_text_stripped[:-3].strip()
    if new_text_stripped.startswith("---"):
        new_text_stripped = new_text_stripped[3:].strip()

    if old_text != new_text_stripped:
        history = seg.get("history", [])
        history.insert(0, {"text": old_text, "updated_at": seg.get("updated_at", now)})
        seg["history"] = history[:history_limit]
        seg["text"] = new_text_stripped
        seg["updated_at"] = now
        save_db(db, name=db_name, db_type=db_type)
        return True
    return False


def resolve_enum_value(db_type: str, key: str, fallback_enum, db_name: str | None = None) -> str:
    effective_db = db_name if db_name else get_active_db_name(db_type)
    lookup = get_db_lookup(db_type, effective_db)
    if key in lookup:
        return lookup[key]
    try:
        return fallback_enum[key].value
    except KeyError:
        return key


_ai_request_history: list[float] = []


def _check_ai_rate_limit() -> None:
    global _ai_request_history
    now = time.time()
    _ai_request_history = [t for t in _ai_request_history if now - t < 60]
    if len(_ai_request_history) >= 10:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for AI requests. Please try again in a minute.",
        )
    _ai_request_history.append(now)


def _resolve_generation_config(
    cfg_data: dict | Any, db_name: str | None = None
) -> GenerationConfig:
    if hasattr(cfg_data, "model_dump"):
        data = cfg_data.model_dump()
    elif hasattr(cfg_data, "dict"):
        data = cfg_data.dict()
    elif isinstance(cfg_data, dict):
        data = cfg_data
    else:
        data = {}

    return GenerationConfig(
        subject=resolve_enum_value("subjects", data.get("subject", "PERSON"), Subject, db_name),
        appearance=resolve_enum_value(
            "appearances", data.get("appearance", "CASUAL"), Appearance, db_name
        ),
        wardrobe=resolve_enum_value("wardrobes", data.get("wardrobe", "CASUAL"), Wardrobe, db_name),
        pose=resolve_enum_value("poses", data.get("pose", "PORTRAIT"), Pose, db_name),
        scene=resolve_enum_value("scenes", data.get("scene", "STUDIO"), Scene, db_name),
        style=resolve_enum_value("styles", data.get("style", ""), Style, db_name),
    )


async def _call_openrouter(messages: list[dict[str, str]], temperature: float = 0.5) -> str:
    _check_ai_rate_limit()

    if not cfg.OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=400,
            detail=(
                "OPENROUTER_API_KEY not configured. Define it in .data/.env "
                "or the process environment."
            ),
        )

    fallback_models = cfg.OPENROUTER_MODELS
    async with httpx.AsyncClient(timeout=30.0) as client:
        for model in fallback_models:
            try:
                logger.info(f"Attempting OpenRouter call with model: {model}")
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}".strip(),
                        "Content-Type": "application/json",
                        "HTTP-Referer": "http://localhost:8889",
                        "X-Title": "SceneQueue",
                    },
                    json={"model": model, "messages": messages, "temperature": temperature},
                )

                if resp.status_code == 200:
                    new_prompt = resp.json()["choices"][0]["message"]["content"].strip()
                    if new_prompt.startswith("```"):
                        lines = new_prompt.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].startswith("```"):
                            lines = lines[:-1]
                        new_prompt = "\n".join(lines).strip()
                    logger.info(f"Successfully generated prompt using {model}")
                    return new_prompt
                else:
                    logger.warning(
                        f"Model {model} failed with status {resp.status_code}: {resp.text}"
                    )
            except Exception as e:
                logger.error(f"Error calling model {model}: {e}")

    raise HTTPException(
        status_code=500,
        detail="All AI models failed to generate a response. Please check your network and API key.",
    )


# =====================================================================
# 1. Frontend & Meta Routes
# =====================================================================
@router.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if FRONTEND_PATH.exists():
        with FRONTEND_PATH.open("r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="frontend.html not found")


@router.get("/manifest.json")
async def serve_manifest():
    manifest_path = cfg.ROOT / "static" / "manifest.json"
    if manifest_path.exists():
        return FileResponse(manifest_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="manifest.json not found")


@router.get("/sw.js")
async def serve_sw():
    sw_path = cfg.ROOT / "static" / "sw.js"
    if sw_path.exists():
        return FileResponse(sw_path, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="sw.js not found")


@router.get("/api/enums")
async def get_enums():
    def load_type_enums(db_type: str, fallback_enum) -> list[dict]:
        lookup = get_db_lookup(db_type, get_active_db_name(db_type))
        if not lookup:
            return [{"value": e.value, "name": e.name} for e in fallback_enum]
        return [{"name": k, "value": v} for k, v in lookup.items()]

    return {
        "subject": load_type_enums("subjects", Subject),
        "appearance": load_type_enums("appearances", Appearance),
        "wardrobe": load_type_enums("wardrobes", Wardrobe),
        "pose": load_type_enums("poses", Pose),
        "scene": load_type_enums("scenes", Scene),
        "style": load_type_enums("styles", Style),
    }


@router.get("/api/templates")
async def get_templates():
    segs = get_all_segments()
    return {"count": len(segs), "db_name": get_active_db_name()}


@router.get("/api/comfy-status")
async def get_comfy_status():
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{cfg.COMFY_URL}/queue")
            if resp.status_code == 200:
                return {"status": "connected"}
    except Exception as e:
        logger.warning(f"ComfyUI not reachable: {e}")
    return {"status": "disconnected"}


# =====================================================================
# 2. Config Routes
# =====================================================================
def _save_and_reload(new_cfg: dict) -> None:
    cfg.save_config(new_cfg)
    cfg.reload_config()


@router.get("/api/config")
async def get_config():
    public_config = cfg.load_config()
    public_config["openrouter_configured"] = bool(cfg.OPENROUTER_API_KEY)
    public_config["openrouter_models"] = cfg.OPENROUTER_MODELS
    return public_config


class ConfigRequest(BaseModel):
    comfy_url: str
    target_node_id: str
    target_input_key: str = "text"
    width: int = Field(ge=64, le=16384)
    height: int = Field(ge=64, le=16384)
    comfy_root: str
    checkpoint: str | None = None
    loras: list[dict] | None = None
    chunk_size: int = Field(default=1, ge=1, le=1)
    sampler_name: str = "dpmpp_2m_sde_heun_gpu"
    scheduler: str = "beta57"
    steps: int = Field(default=12, ge=1, le=1000)
    cfg_scale: float = Field(default=1.0, ge=0, le=100)
    denoise: float = Field(default=1.0, ge=0, le=1)
    highres_enabled: bool = True
    highres_scale: float = Field(default=1.5, ge=1, le=4)
    highres_steps: int = Field(default=4, ge=1, le=1000)
    highres_cfg_scale: float = Field(default=1.6, ge=0, le=100)
    highres_denoise: float = Field(default=0.45, ge=0, le=1)
    adult_content: bool = False
    openrouter_api_key: str | None = None
    openrouter_clear_key: bool = False
    openrouter_models: list[str] = Field(default_factory=list)


@router.post("/api/config")
async def update_config(cfg_req: ConfigRequest):
    new_cfg = {
        "comfy_url": cfg_req.comfy_url,
        "target_node_id": cfg_req.target_node_id,
        "target_input_key": cfg_req.target_input_key,
        "width": cfg_req.width,
        "height": cfg_req.height,
        "comfy_root": cfg_req.comfy_root,
        "checkpoint": cfg_req.checkpoint or "",
        "loras": cfg_req.loras or [],
        "chunk_size": cfg_req.chunk_size,
        "sampler_name": cfg_req.sampler_name,
        "scheduler": cfg_req.scheduler,
        "steps": cfg_req.steps,
        "cfg_scale": cfg_req.cfg_scale,
        "denoise": cfg_req.denoise,
        "highres_enabled": cfg_req.highres_enabled,
        "highres_scale": cfg_req.highres_scale,
        "highres_steps": cfg_req.highres_steps,
        "highres_cfg_scale": cfg_req.highres_cfg_scale,
        "highres_denoise": cfg_req.highres_denoise,
        "adult_content": cfg_req.adult_content,
    }
    try:
        cfg.save_config(new_cfg)
        cfg.save_openrouter_settings(
            cfg_req.openrouter_api_key,
            cfg_req.openrouter_models,
            clear_key=cfg_req.openrouter_clear_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    cfg.reload_config()
    return {"status": "ok"}


async def _fetch_comfy_catalog() -> dict[str, list[str]]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        checkpoints = []
        try:
            resp = await client.get(f"{cfg.COMFY_URL}/object_info/CheckpointLoaderKJ")
            if resp.status_code == 200:
                data = resp.json()
                checkpoints = (
                    data.get("CheckpointLoaderKJ", {})
                    .get("input", {})
                    .get("required", {})
                    .get("ckpt_name", [[]])[0]
                )
        except Exception as e:
            logger.warning(f"Failed to fetch CheckpointLoaderKJ metadata: {e}")

        if not checkpoints:
            try:
                resp = await client.get(f"{cfg.COMFY_URL}/object_info/CheckpointLoaderSimple")
                if resp.status_code == 200:
                    data = resp.json()
                    checkpoints = (
                        data.get("CheckpointLoaderSimple", {})
                        .get("input", {})
                        .get("required", {})
                        .get("ckpt_name", [[]])[0]
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch CheckpointLoaderSimple metadata: {e}")

        loras = []
        try:
            resp = await client.get(f"{cfg.COMFY_URL}/object_info/LoraLoader")
            if resp.status_code == 200:
                data = resp.json()
                loras = (
                    data.get("LoraLoader", {})
                    .get("input", {})
                    .get("required", {})
                    .get("lora_name", [[]])[0]
                )
        except Exception as e:
            logger.warning(f"Failed to fetch LoraLoader metadata: {e}")

        samplers = []
        schedulers = []
        try:
            resp = await client.get(f"{cfg.COMFY_URL}/object_info/KSampler")
            if resp.status_code == 200:
                required = resp.json().get("KSampler", {}).get("input", {}).get("required", {})
                samplers = required.get("sampler_name", [[]])[0]
                schedulers = required.get("scheduler", [[]])[0]
        except Exception as e:
            logger.warning(f"Failed to fetch KSampler metadata: {e}")

        return {
            "checkpoints": checkpoints if isinstance(checkpoints, list) else [],
            "loras": loras if isinstance(loras, list) else [],
            "samplers": samplers if isinstance(samplers, list) else [],
            "schedulers": schedulers if isinstance(schedulers, list) else [],
        }


@router.get("/api/comfy-models")
async def get_comfy_models():
    catalog = await _fetch_comfy_catalog()
    catalog["recommended_preset"] = {
        "name": "Fast Illustration",
        "checkpoint": RECOMMENDED_CHECKPOINT,
        "lora": RECOMMENDED_LORA,
        "sampler_name": "dpmpp_2m_sde_heun_gpu",
        "scheduler": "beta57",
        "steps": 12,
        "cfg_scale": 1.0,
        "denoise": 1.0,
        "highres_enabled": True,
        "highres_scale": 1.5,
        "highres_steps": 4,
        "highres_cfg_scale": 1.6,
        "highres_denoise": 0.45,
    }
    return catalog


# =====================================================================
# 3. Databases Routes
# =====================================================================
@router.get("/api/databases")
async def get_databases(db_type: str = Query("prompts", alias="type")):
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    dbs = []
    target_dir = DATABASES_DIR / db_type
    for f in sorted(target_dir.glob("*.json")):
        try:
            with f.open("r", encoding="utf-8") as fh:
                db = json.load(fh)
            total_segs = len(db.get("segments", []))
            dbs.append(
                {
                    "name": f.stem,
                    "segments": total_segs,
                    "updated_at": db.get("updated_at", ""),
                    "active": f.stem == get_active_db_name(db_type),
                }
            )
        except Exception:
            dbs.append(
                {
                    "name": f.stem,
                    "segments": 0,
                    "updated_at": "",
                    "active": f.stem == get_active_db_name(db_type),
                }
            )
    return dbs


class SetActiveDbRequest(BaseModel):
    name: str
    type: str = "prompts"


@router.post("/api/databases/active")
async def set_active_database(req: SetActiveDbRequest):
    db_type = req.type
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    name = _validate_database_name(req.name)
    target = DATABASES_DIR / db_type / f"{name}.json"
    if not target.exists():
        db = {"version": 2, "segments": [], "updated_at": ""}
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
    set_active_db_name(db_type, name)
    return {"status": "ok", "active": name}


@router.delete("/api/databases/{name}")
async def delete_database(name: str, db_type: str = Query("prompts", alias="type")):
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    name = _validate_database_name(name)
    if name == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the default database")
    active = get_active_db_name(db_type)
    if name == active:
        raise HTTPException(
            status_code=400, detail="Cannot delete the active database. Switch first."
        )

    target = DATABASES_DIR / db_type / f"{name}.json"
    if not target.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    try:
        shutil.move(str(target), f"/tmp/{name}_{uuid.uuid4().hex}.json")
    except Exception as e:
        logger.error(f"Failed to backup database to /tmp: {e}")
        raise HTTPException(
            status_code=500,
            detail="Could not move the database to the temporary backup directory.",
        ) from e

    return {"status": "ok", "deleted": name}


# =====================================================================
# 4. Prompt / Segment Management Routes
# =====================================================================
@router.get("/api/prompts")
async def get_prompts(db_type: str = Query("prompts", alias="type")):
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    db = load_db(db_type=db_type)
    segments = sorted(db.get("segments", []), key=lambda s: s.get("index", 0))
    return {"segments": segments, "total": len(segments)}


class PromptUpdateRequest(BaseModel):
    content: str


@router.post("/api/prompts")
async def update_prompt(req: PromptUpdateRequest, db_type: str = Query("prompts", alias="type")):
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    db = load_db(db_type=db_type)
    new_texts = [s.strip() for s in re.split(r"\n\s*-{3,}\s*\n", req.content) if s.strip()]
    existing = db.get("segments", [])
    updated = []
    for i, text in enumerate(new_texts):
        updated.append(
            {
                "index": i,
                "text": text,
                "history": existing[i].get("history", []) if i < len(existing) else [],
            }
        )
    db["segments"] = updated
    save_db(db, db_type=db_type)
    return {"status": "ok", "total": len(updated)}


class SegmentUpdateRequest(BaseModel):
    index: int
    text: str


@router.post("/api/prompts/segment")
async def update_segment(req: SegmentUpdateRequest, db_type: str = Query("prompts", alias="type")):
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    active_db = get_active_db_name(db_type)
    db = load_db(active_db, db_type=db_type)
    segs = db.get("segments", [])
    if req.index < 0 or req.index >= len(segs):
        raise HTTPException(status_code=404, detail="Segment index out of range")

    _update_db_segment(db, req.index, req.text, history_limit=5, db_name=active_db, db_type=db_type)
    return {"status": "ok", "index": req.index}


@router.delete("/api/prompts/{index}")
async def delete_segment(
    index: int, db_type: str = Query("prompts", alias="type"), session_id: str | None = None
):
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    db = load_db(db_type=db_type)
    segs = sorted(db.get("segments", []), key=lambda s: s.get("index", 0))
    if index < 0 or index >= len(segs):
        raise HTTPException(status_code=404, detail="Segment index out of range")
    segs.pop(index)
    for i, seg in enumerate(segs):
        seg["index"] = i
    db["segments"] = segs
    save_db(db, db_type=db_type)

    if db_type == "prompts":
        db_name = get_active_db_name(db_type)
        async with state_lock:
            st = load_state()
            new_st = []
            deleted_ids = []

            # Find all sessions using this database
            target_sessions = set()
            for entry in st:
                if entry.get("db_name") == db_name:
                    sid = entry.get("session_id")
                    if sid:
                        target_sessions.add(sid)

            for entry in st:
                if entry.get("db_name") == db_name and entry.get("session_id") in target_sessions:
                    idx = entry.get("segment_index")
                    if idx == index:
                        deleted_ids.append(entry["id"])
                        continue
                    elif idx is not None and idx > index:
                        entry["segment_index"] = idx - 1
                new_st.append(entry)
            save_state(new_st)

            for rid in deleted_ids:
                for d in [IMAGES_DIR, THUMBS_DIR]:
                    for ext in [".png", ".jpg"]:
                        p = d / f"{rid}{ext}"
                        p.unlink(missing_ok=True)

    return {"status": "ok", "deleted_index": index, "remaining": len(segs)}


class AddSegmentRequest(BaseModel):
    text: str = ""
    insert_at: int | None = None
    session_id: str | None = None


@router.post("/api/prompts/add")
async def add_segment(req: AddSegmentRequest, db_type: str = Query("prompts", alias="type")):
    if db_type not in cfg.DB_TYPES:
        db_type = "prompts"
    db = load_db(db_type=db_type)
    segs = sorted(db.get("segments", []), key=lambda s: s.get("index", 0))

    if req.insert_at is not None:
        new_idx = max(0, min(req.insert_at, len(segs)))
        segs.insert(new_idx, {"index": new_idx, "text": req.text.strip(), "history": []})
        for i, seg in enumerate(segs):
            seg["index"] = i
    else:
        new_idx = len(segs)
        segs.append({"index": new_idx, "text": req.text.strip(), "history": []})

    db["segments"] = segs
    save_db(db, db_type=db_type)

    if db_type == "prompts":
        db_name = get_active_db_name(db_type)
        async with state_lock:
            st = load_state()

            # Find all sessions using this database
            target_sessions = set()
            for entry in st:
                if entry.get("db_name") == db_name:
                    sid = entry.get("session_id")
                    if sid:
                        target_sessions.add(sid)

            if req.session_id:
                target_sessions.add(req.session_id)

            insert_position = req.insert_at if req.insert_at is not None else new_idx

            for sid in target_sessions:
                # Shift indices
                for entry in st:
                    if entry.get("session_id") == sid:
                        idx = entry.get("segment_index", 0)
                        if req.insert_at is not None:
                            if idx >= req.insert_at:
                                entry["segment_index"] = idx + 1

                # Get configuration from sessions store (isolated, no cross-DB contamination)
                session = get_session(sid)
                session_config = session["subject_config"] if session else {}
                chunk_size_val = 1

                new_id = str(uuid.uuid4())
                new_item = {
                    "id": new_id,
                    "session_id": sid,
                    "parent_id": None,
                    "db_name": db_name,
                    "segment_index": insert_position,
                    "prompt_resolved": req.text.strip()
                    or "{style}, {subject}, {appearance}, {wardrobe}, {pose}, {scene}",
                    "chunk_number": f"insert_{new_id}",
                    "prompt_id": "",
                    "status": "failed",
                    "filename": "",
                    "image_index": 0,
                    "config": session_config,
                    "chunk_size": chunk_size_val,
                    "upscaled": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                st.append(new_item)

            save_state(st)

    return {"status": "ok", "index": new_idx, "total": len(segs)}


@router.get("/api/item-template/{item_id}")
async def get_item_template(item_id: str):
    async with state_lock:
        st = load_state()
    item = next((i for i in st if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    seg_idx = item.get("segment_index")
    if seg_idx is not None:
        segs = get_all_segments()
        if seg_idx < len(segs):
            return {
                "prompt_original": segs[seg_idx]["text"],
                "db_name": item.get("db_name", ""),
                "segment_index": seg_idx,
                "history": segs[seg_idx].get("history", []),
                "config": item.get("config", {}),
            }

    return {
        "prompt_original": "",
        "db_name": item.get("db_name", ""),
        "segment_index": item.get("segment_index", 0),
        "history": [],
        "config": item.get("config", {}),
    }


@router.get("/api/autocomplete-tags")
async def get_autocomplete_tags():
    segs = get_all_segments()
    tags = set()
    for s in segs:
        text = s.get("text", "")
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            clean = part
            while clean.startswith("("):
                clean = clean[1:]
            while clean.endswith(")"):
                clean = clean[:-1]
            if ":" in clean:
                clean = clean.split(":")[0]
            while clean.startswith("["):
                clean = clean[1:]
            while clean.endswith("]"):
                clean = clean[:-1]
            clean = clean.strip()
            if clean:
                tags.add(clean)
    return sorted(list(tags), key=str.lower)


# =====================================================================
# 5. Image Generation Routes
# =====================================================================
class BatchRequest(BaseModel):
    subject: str
    appearance: str
    wardrobe: str
    pose: str
    scene: str
    style: str = ""


@router.post("/api/generate-batch")
async def generate_batch(req: BatchRequest):
    config = _resolve_generation_config(req)

    session_id = str(uuid.uuid4())
    all_segments = get_all_segments()
    if not all_segments:
        raise HTTPException(status_code=400, detail="No segments found in database")

    if not cfg.CHECKPOINT:
        catalog = await _fetch_comfy_catalog()
        checkpoints = catalog["checkpoints"]
        if not checkpoints:
            raise HTTPException(
                status_code=400,
                detail="ComfyUI did not report any installed checkpoints.",
            )
        selected = (
            RECOMMENDED_CHECKPOINT if RECOMMENDED_CHECKPOINT in checkpoints else checkpoints[0]
        )
        saved_config = cfg.load_config()
        saved_config["checkpoint"] = selected
        cfg.save_config(saved_config)
        cfg.reload_config()
        logger.info(f"Automatically selected checkpoint {selected}")

    client = ComfyClient.setup(cfg.COMFY_URL)
    resolver = PromptResolver.setup(config)
    chunk_size = cfg.CHUNK_SIZE
    new_items = []
    queue_errors = []
    global_idx = 0
    db_name = get_active_db_name()

    for chunk_start in range(0, len(all_segments), chunk_size):
        chunk = all_segments[chunk_start : chunk_start + chunk_size]
        chunk_texts = [s["text"] for s in chunk]
        chunk_count = len(chunk_texts)
        chunk_number = str(chunk_start // chunk_size)

        full_original = join_segments(chunk_texts)
        resolved_text = resolver.resolve_text(full_original)
        try:
            wf = build_batch(resolved_text, chunk_number, session_id)
            prompt_id = await client.queue_prompt(wf)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except ComfyQueueError as e:
            logger.error(f"Failed to queue generation batch chunk {chunk_number}: {e}")
            queue_errors.append(str(e))
            break
        except Exception as e:
            logger.error(f"Failed to queue generation batch chunk {chunk_number}: {e}")
            queue_errors.append(f"Could not reach ComfyUI: {e}")
            break

        for seg_idx in range(chunk_count):
            item = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "parent_id": None,
                "db_name": db_name,
                "segment_index": global_idx + seg_idx,
                "prompt_resolved": resolved_text,
                "chunk_number": chunk_number,
                "prompt_id": prompt_id,
                "status": "pending",
                "filename": "",
                "chunk_size": chunk_count,
                "image_index": seg_idx,
                "upscaled": False,
                "config": {
                    "subject": req.subject,
                    "appearance": req.appearance,
                    "wardrobe": req.wardrobe,
                    "pose": req.pose,
                    "scene": req.scene,
                    "style": req.style,
                },
                "width": (
                    round(cfg.WIDTH * cfg.HIGHRES_SCALE / 8) * 8
                    if cfg.HIGHRES_ENABLED
                    else cfg.WIDTH
                ),
                "height": (
                    round(cfg.HEIGHT * cfg.HIGHRES_SCALE / 8) * 8
                    if cfg.HIGHRES_ENABLED
                    else cfg.HEIGHT
                ),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            new_items.append(item)
        global_idx += chunk_count

    if not new_items:
        detail = queue_errors[0] if queue_errors else "No generation jobs were queued."
        raise HTTPException(status_code=502, detail=detail)

    upsert_session(
        session_id,
        db_name,
        {
            "subject": req.subject,
            "appearance": req.appearance,
            "wardrobe": req.wardrobe,
            "pose": req.pose,
            "scene": req.scene,
            "style": req.style,
        },
    )

    async with state_lock:
        st = load_state()
        st = new_items + st
        save_state(st)

    return {
        "session_id": session_id,
        "items": new_items,
        "count": len(new_items),
        "warnings": queue_errors,
    }


class AIPromptRequest(BaseModel):
    original_prompt: str
    instruction: str
    ai_suggestion: str | None = None
    previous_instruction: str | None = None


@router.post("/api/ai/preview-prompt")
async def ai_preview_prompt(req: AIPromptRequest):
    if not cfg.OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="OPENROUTER_API_KEY not configured. Define it in .env or system environment.",
        )

    guidelines_path = cfg.ROOT / "llm" / "prompt_guidelines.md"
    guidelines = ""
    if guidelines_path.exists():
        try:
            guidelines = guidelines_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read prompt guidelines: {e}")

    system_instruction = (
        "You are an expert prompt editor for text-to-image generation models.\n"
        "Your task is to edit the provided prompt segment based on the user's instructions.\n"
        "Follow these guidelines strictly:\n"
        "---\n"
        f"{guidelines}\n"
        "---\n"
        "CRITICAL RULES:\n"
        "- Respond only with the final prompt. Do not add quotes, Markdown, or explanations.\n"
        "- Preserve placeholders such as {subject}, {appearance}, {wardrobe}, {pose}, {scene}, and {style}.\n"
        "- Do NOT modify parts of the prompt that are unrelated to the user's instructions."
    )

    messages = [{"role": "system", "content": system_instruction}]
    if req.previous_instruction and req.ai_suggestion:
        messages.append(
            {
                "role": "user",
                "content": f"Original Prompt:\n{req.original_prompt}\n\nInstruction:\n{req.previous_instruction}",
            }
        )
        messages.append({"role": "assistant", "content": req.ai_suggestion})
        messages.append(
            {"role": "user", "content": f"Follow-up Adjustment Instruction:\n{req.instruction}"}
        )
    else:
        messages.append(
            {
                "role": "user",
                "content": f"Original Prompt:\n{req.original_prompt}\n\nInstruction:\n{req.instruction}",
            }
        )

    new_prompt = await _call_openrouter(messages, temperature=0.5)
    return {"original_prompt": req.original_prompt, "new_prompt": new_prompt}


class EditPromptRequest(BaseModel):
    item_id: str
    prompt: str
    config: dict[str, str] | None = None


@router.post("/api/edit-prompt")
async def edit_prompt(req: EditPromptRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    async with state_lock:
        st = load_state()
        item = next((i for i in st if i["id"] == req.item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        seg_idx = item.get("segment_index", 0)
        db_name = item.get("db_name", "default")
        item_config = item.get("config", {})
        session_id = item.get("session_id", str(uuid.uuid4()))

    # Update database
    db = load_db(db_name)
    _update_db_segment(db, seg_idx, req.prompt, history_limit=3, db_name=db_name)

    # Resolve
    cfg_data = req.config if req.config is not None else item_config
    try:
        resolver_cfg = _resolve_generation_config(cfg_data, db_name)
    except Exception:
        resolved_prompt = wrap_segment(req.prompt)
    else:
        resolver = PromptResolver.setup(resolver_cfg)
        resolved_prompt = resolver.resolve_text(wrap_segment(req.prompt))

    edit_folder = f"edit_{str(uuid.uuid4())}"

    wf = build_batch(resolved_prompt, edit_folder, session_id)
    client = ComfyClient.setup(cfg.COMFY_URL)
    try:
        prompt_id = await client.queue_prompt(wf)
    except Exception as e:
        logger.error(f"Failed to queue edit prompt for item {req.item_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to queue: {e}")

    # Overwrite the existing item in place
    async with state_lock:
        st = load_state()
        entry = next((i for i in st if i["id"] == req.item_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Item not found")
        entry["prompt_resolved"] = resolved_prompt
        entry["prompt_id"] = prompt_id
        entry["status"] = "pending"
        entry["filename"] = ""
        entry["chunk_number"] = edit_folder
        entry["image_index"] = 0
        entry["upscaled"] = False
        if req.config is not None:
            entry["config"] = req.config
        save_state(st)

    # Delete old image files from disk
    item_id = req.item_id
    for d in [IMAGES_DIR, THUMBS_DIR]:
        for ext in [".png", ".jpg"]:
            p = d / f"{item_id}{ext}"
            if p.exists():
                p.unlink(missing_ok=True)

    return {"id": item_id, "status": "pending", "prompt_resolved": resolved_prompt}


class InsertJobRequest(BaseModel):
    item_id: str
    position: str  # "before" or "after"
    use_ai: bool = False
    instruction: str | None = None
    count: int = 1


@router.post("/api/insert-segment-job")
async def insert_segment_job(req: InsertJobRequest):
    if req.position not in ("before", "after"):
        raise HTTPException(status_code=400, detail="Invalid position")
    if req.count < 1:
        raise HTTPException(status_code=400, detail="Count must be at least 1")

    async with state_lock:
        st = load_state()
        item = next((i for i in st if i["id"] == req.item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        seg_idx = item.get("segment_index")
        if seg_idx is None:
            raise HTTPException(status_code=400, detail="Item does not have a segment index")

        db_name = item.get("db_name", "default")
        session_id = item.get("session_id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Item does not have a session id")

        target_sessions = set()
        for entry in st:
            if entry.get("db_name") == db_name:
                sid = entry.get("session_id")
                if sid:
                    target_sessions.add(sid)
        target_sessions.add(session_id)

        # Ensure source session is registered in sessions store
        cfg_data = item.get("config", {})
        if cfg_data:
            upsert_session(session_id, db_name, cfg_data)

        last_new_id = None
        last_insert_at = None
        inserted_ids = set()

        for step in range(req.count):
            db = load_db(db_name)
            segs = sorted(db.get("segments", []), key=lambda s: s.get("index", 0))

            insert_at = seg_idx + step if req.position == "before" else seg_idx + step + 1
            last_insert_at = insert_at

            preceding_prompt = None
            succeeding_prompt = None
            if insert_at > 0 and insert_at - 1 < len(segs):
                preceding_prompt = segs[insert_at - 1].get("text", "")
            if insert_at < len(segs):
                succeeding_prompt = segs[insert_at].get("text", "")

            default_text = "{style}, {subject}, {appearance}, {wardrobe}, {pose}, {scene}"
            if req.use_ai:
                guidelines_path = cfg.ROOT / "llm" / "prompt_guidelines.md"
                guidelines = ""
                if guidelines_path.exists():
                    try:
                        guidelines = guidelines_path.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.error(f"Failed to read prompt guidelines: {e}")

                system_instruction = (
                    "You are an expert prompt editor for text-to-image generation models.\n"
                    "Your task is to generate a new prompt segment to be inserted in a sequence of images/frames.\n"
                    "Follow these guidelines strictly:\n"
                    "---\n"
                    f"{guidelines}\n"
                    "---\n"
                    "CRITICAL RULES:\n"
                    "- Respond only with the final prompt. Do not add quotes, Markdown, or explanations.\n"
                    "- Keep placeholders like {subject}, {appearance}, {wardrobe}, {pose}, {scene}, and {style} consistent.\n"
                    "- Preserve visual continuity between adjacent frames.\n"
                    "- Use concise, model-neutral descriptive language."
                )

                user_content = ""
                if preceding_prompt:
                    user_content += f"Preceding Frame Prompt:\n{preceding_prompt}\n\n"
                if succeeding_prompt:
                    user_content += f"Succeeding Frame Prompt:\n{succeeding_prompt}\n\n"

                if req.instruction:
                    if req.count > 1:
                        user_content += (
                            f"Instruction/Action for the new frame sequence:\n{req.instruction}\n"
                        )
                        user_content += f"This is frame {step + 1} of {req.count} that we are inserting sequentially.\n\n"
                    else:
                        user_content += (
                            f"Instruction/Action for the new frame:\n{req.instruction}\n\n"
                        )
                else:
                    if req.count > 1:
                        user_content += f"Create a logical transition/continuation frame between the preceding and succeeding frames (Frame {step + 1} of {req.count} in progress).\n\n"
                    else:
                        user_content += "Create a logical transition/continuation frame between the preceding and succeeding frames.\n\n"

                user_content += "Please output the generated prompt segment."

                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_content},
                ]

                default_text = await _call_openrouter(messages, temperature=0.6)

            new_seg = {"index": insert_at, "text": default_text, "history": []}
            segs.insert(insert_at, new_seg)
            for i, s in enumerate(segs):
                s["index"] = i
            db["segments"] = segs
            save_db(db, db_name)

            # 2. Resolve prompt text
            cfg_data = item.get("config", {})
            try:
                resolver_cfg = _resolve_generation_config(cfg_data, db_name)
                resolver = PromptResolver.setup(resolver_cfg)
                resolved_prompt = resolver.resolve_text(wrap_segment(default_text))
            except Exception as e:
                logger.error(f"Failed to resolve prompt for insertion: {e}")
                resolved_prompt = wrap_segment(default_text)

            new_id = str(uuid.uuid4())
            last_new_id = new_id
            chunk_number = f"insert_{new_id}"

            # 3. Queue in ComfyUI
            client = ComfyClient.setup(cfg.COMFY_URL)
            wf = build_batch(resolved_prompt, chunk_number, session_id)
            try:
                prompt_id = await client.queue_prompt(wf)
            except Exception as e:
                logger.error(f"Failed to queue inserted segment job: {e}")
                prompt_id = ""

            new_item = {
                "id": new_id,
                "session_id": session_id,
                "parent_id": None,
                "db_name": db_name,
                "segment_index": insert_at,
                "prompt_resolved": resolved_prompt,
                "chunk_number": chunk_number,
                "prompt_id": prompt_id,
                "status": "pending" if prompt_id else "failed",
                "filename": "",
                "image_index": 0,
                "config": cfg_data,
                "upscaled": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            inserted_ids.add(new_id)

            # 4. Update state: shift indices for all sessions, then append new items
            # IMPORTANT: shift must happen BEFORE appending new items to avoid
            # the new item being accidentally shifted by the same loop.
            for sid in target_sessions:
                for entry in st:
                    if entry["id"] in inserted_ids:
                        continue
                    if entry.get("session_id") == sid:
                        if entry.get("segment_index", 0) >= insert_at:
                            entry["segment_index"] += 1

            # Append the real new item for the primary session
            st.append(new_item)

            # Append placeholder items for other sessions (with per-session prompt resolution)
            for sid in target_sessions:
                if sid == session_id:
                    continue
                session = get_session(sid)
                session_config = session["subject_config"] if session else {}
                chunk_size_val = 1

                # Resolve prompt using THIS session's own config (not the source item's)
                if session_config:
                    try:
                        session_resolver_cfg = _resolve_generation_config(
                            session_config, session["db_name"]
                        )
                        session_resolver = PromptResolver.setup(session_resolver_cfg)
                        session_resolved = session_resolver.resolve_text(wrap_segment(default_text))
                    except Exception as e:
                        logger.error(f"Failed to resolve prompt for session {sid}: {e}")
                        session_resolved = wrap_segment(default_text)
                else:
                    session_resolved = wrap_segment(default_text)

                placeholder_id = str(uuid.uuid4())
                placeholder_item = {
                    "id": placeholder_id,
                    "session_id": sid,
                    "parent_id": None,
                    "db_name": db_name,
                    "segment_index": insert_at,
                    "prompt_resolved": session_resolved,
                    "chunk_number": f"insert_{placeholder_id}",
                    "prompt_id": "",
                    "status": "failed",
                    "filename": "",
                    "image_index": 0,
                    "config": session_config,
                    "chunk_size": chunk_size_val,
                    "upscaled": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                inserted_ids.add(placeholder_id)
                st.append(placeholder_item)

        save_state(st)

    return {"status": "ok", "new_item_id": last_new_id, "insert_at": last_insert_at}


class DeleteJobRequest(BaseModel):
    item_id: str


@router.post("/api/delete-segment-job")
async def delete_segment_job(req: DeleteJobRequest):
    async with state_lock:
        st = load_state()
        item = next((i for i in st if i["id"] == req.item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        seg_idx = item.get("segment_index")
        if seg_idx is None:
            raise HTTPException(status_code=400, detail="Item does not have a segment index")

        db_name = item.get("db_name", "default")
        session_id = item.get("session_id")
        if not session_id:
            raise HTTPException(status_code=400, detail="Item does not have a session id")

        # 1. Delete from database
        db = load_db(db_name)
        segs = db.get("segments", [])
        if 0 <= seg_idx < len(segs):
            segs.pop(seg_idx)
            for i, s in enumerate(segs):
                s["index"] = i
            db["segments"] = segs
            save_db(db, db_name)

        # 2. Delete items from state.json and update index of subsequent items for all sessions using this database
        new_st = []
        deleted_ids = [req.item_id]

        target_sessions = set()
        for entry in st:
            if entry.get("db_name") == db_name:
                sid = entry.get("session_id")
                if sid:
                    target_sessions.add(sid)

        for entry in st:
            if entry["id"] == req.item_id:
                continue

            if entry.get("db_name") == db_name and entry.get("session_id") in target_sessions:
                idx = entry.get("segment_index")
                if idx == seg_idx:
                    deleted_ids.append(entry["id"])
                    continue
                elif idx is not None and idx > seg_idx:
                    entry["segment_index"] = idx - 1
            new_st.append(entry)
        save_state(new_st)

    # 3. Delete old image files from disk
    for item_id in deleted_ids:
        for d in [IMAGES_DIR, THUMBS_DIR]:
            for ext in [".png", ".jpg"]:
                p = d / f"{item_id}{ext}"
                p.unlink(missing_ok=True)

    return {"status": "ok", "deleted_index": seg_idx}


# =====================================================================
# 6. Gallery & Session Routes
# =====================================================================
@router.get("/api/state")
async def get_state(
    limit: int = 50, offset: int = 0, root_id: str | None = None, session_id: str | None = None
):
    async with state_lock:
        st = load_state()
    if session_id:
        items = [i for i in st if i.get("session_id") == session_id]

        def _sort_key(item):
            seg_idx = item.get("segment_index", 0)
            is_upscaled = 1 if item.get("upscaled") else 0
            return (seg_idx, is_upscaled)

        items.sort(key=_sort_key)
        res_items = items[offset : offset + limit]
    elif root_id:
        root = next((i for i in st if i["id"] == root_id), None)
        if not root:
            raise HTTPException(status_code=404, detail="Image not found")

        def get_descendants(pid):
            children = []
            for item in st:
                if item.get("parent_id") == pid:
                    children.append(item)
                    children.extend(get_descendants(item["id"]))
            return children

        subtree = [root] + get_descendants(root_id)
        res_items = subtree[offset : offset + limit]
    else:
        res_items = st[offset : offset + limit]

    for item in res_items:
        if item.get("status") == "pending":
            pid = item.get("prompt_id")
            if pid and pid in cfg.active_progress:
                item["progress"] = cfg.active_progress[pid].get("progress", 0.0)
                item["active_node"] = cfg.active_progress[pid].get("node")

    return res_items


@router.get("/api/sessions")
async def get_sessions():
    async with state_lock:
        st = load_state()

    sessions: dict[str, dict] = {}
    for item in st:
        sid = item.get("session_id", "")
        if not sid:
            continue
        if sid not in sessions:
            sessions[sid] = {
                "session_id": sid,
                "config": item.get("config", {}),
                "total": 0,
                "completed": 0,
                "pending": 0,
                "failed": 0,
                "preview_id": None,
            }
        s = sessions[sid]
        if item.get("parent_id") is None:
            s["total"] += 1
            status = item.get("status", "pending")
            if status == "completed":
                s["completed"] += 1
                if not s["preview_id"]:
                    s["preview_id"] = item["id"]
            elif status == "pending":
                s["pending"] += 1
            elif status == "failed":
                s["failed"] += 1
        else:
            if item.get("status") == "completed" and not s["preview_id"]:
                s["preview_id"] = item["id"]

    seen = set()
    result = []
    for item in st:
        sid = item.get("session_id", "")
        if sid and sid not in seen:
            seen.add(sid)
            result.append(sessions[sid])
    return result


class DeleteRequest(BaseModel):
    ids: list[str]
    purge: bool = False


@router.post("/api/items/delete")
async def delete_items(req: DeleteRequest):
    async with state_lock:
        st = load_state()
        all_removed = set()

        for item_id in req.ids:
            all_removed.add(item_id)

            # Find the parent of the item we are deleting
            parent_id = None
            for item in st:
                if item["id"] == item_id:
                    parent_id = item.get("parent_id")
                    break

            # Reparent all immediate children of item_id to parent_id
            for item in st:
                if item.get("parent_id") == item_id:
                    item["parent_id"] = parent_id

        prompt_ids_to_cancel = set()
        for item in st:
            if item["id"] in all_removed and item.get("prompt_id"):
                prompt_ids_to_cancel.add(item["prompt_id"])

        should_interrupt = False
        if prompt_ids_to_cancel:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    queue_data = await client.get(f"{cfg.COMFY_URL}/queue")
                    queue_json = queue_data.json()
                    running_ids = {q[1] for q in queue_json.get("queue_running", [])}
                    if prompt_ids_to_cancel & running_ids:
                        should_interrupt = True
            except Exception as e:
                logger.error(f"Failed to check running jobs: {e}")

        async with httpx.AsyncClient(timeout=5.0) as client:
            if should_interrupt:
                try:
                    await client.post(f"{cfg.COMFY_URL}/interrupt")
                except Exception as e:
                    logger.error(f"Failed to interrupt ComfyUI: {e}")
            for pid in prompt_ids_to_cancel:
                try:
                    await client.post(f"{cfg.COMFY_URL}/queue", json={"delete": [pid]})
                except Exception as e:
                    logger.error(f"Failed to delete job {pid} from ComfyUI queue: {e}")

        if req.purge:
            new_st = [item for item in st if item["id"] not in all_removed]
            save_state(new_st)
        else:
            for item in st:
                if item["id"] in all_removed:
                    item["status"] = "failed"
            save_state(st)

    for rid in all_removed:
        for d in [IMAGES_DIR, THUMBS_DIR]:
            for ext in [".png", ".jpg"]:
                p = d / f"{rid}{ext}"
                if p.exists():
                    p.unlink(missing_ok=True)

    return {"status": "ok", "deleted_count": len(all_removed), "interrupted": should_interrupt}


@router.get("/thumbnails/{item_id}.jpg")
async def serve_thumbnail(item_id: str):
    thumb_path = THUMBS_DIR / f"{item_id}.jpg"
    if thumb_path.exists():
        return FileResponse(thumb_path)

    img_path = IMAGES_DIR / f"{item_id}.png"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    generate_thumbnail(img_path, thumb_path)
    if thumb_path.exists():
        return FileResponse(thumb_path)
    raise HTTPException(status_code=404, detail="Thumbnail not found")


@router.get("/images/{item_id}.png")
async def serve_image(item_id: str):
    local_path = IMAGES_DIR / f"{item_id}.png"
    if local_path.exists():
        return FileResponse(local_path)
    return await get_image_data(item_id)


@router.get("/api/image-data/{item_id}")
async def get_image_data(item_id: str):
    async with state_lock:
        st = load_state()
    item = next((i for i in st if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    local_path = IMAGES_DIR / f"{item_id}.png"
    if local_path.exists():
        return FileResponse(local_path)

    session_id = item.get("session_id", "")
    chunk_number = item.get("chunk_number", "0")
    comfy_output = cfg.OUTPUT_DIR / session_id / chunk_number
    if comfy_output.exists():
        images = sorted(
            [p for p in comfy_output.iterdir() if p.suffix.lower() in (".png", ".jpg", ".webp")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if images:
            return FileResponse(images[0])

    raise HTTPException(status_code=404, detail="Image file not found")


# =====================================================================
# 7. Upscale Routes
# =====================================================================
@router.post("/api/upscale-item")
async def upscale_item(item_id: str):
    async with state_lock:
        st = load_state()
    item = next((i for i in st if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.get("upscaled"):
        raise HTTPException(status_code=400, detail="Already upscaled")

    chunk_number = item.get("chunk_number", "0")
    image_index = item.get("image_index", 0)
    session_id = item.get("session_id", str(uuid.uuid4()))
    wf = build_upscale(session_id, chunk_number, image_index=image_index)

    client = ComfyClient.setup(cfg.COMFY_URL)
    try:
        prompt_id = await client.queue_prompt(wf)
    except Exception as e:
        logger.error(f"Failed to queue upscale for item {item_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to queue upscale: {e}")

    new_item = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "parent_id": item_id,
        "db_name": item.get("db_name", ""),
        "segment_index": item.get("segment_index", 0),
        "prompt_resolved": f"[upscale] {item.get('prompt_resolved', '')}",
        "chunk_number": chunk_number,
        "prompt_id": prompt_id,
        "status": "pending",
        "filename": "",
        "upscaled": True,
        "image_index": image_index,
        "config": item.get("config", {}),
        "width": cfg.WIDTH * 4,
        "height": cfg.HEIGHT * 4,
    }

    async with state_lock:
        st = load_state()
        st.insert(0, new_item)
        save_state(st)

    return new_item


class UpscaleSessionRequest(BaseModel):
    session_id: str


@router.post("/api/upscale-session")
async def upscale_session(req: UpscaleSessionRequest):
    async with state_lock:
        st = load_state()

    targets = [
        i
        for i in st
        if i.get("session_id") == req.session_id
        and i.get("status") == "completed"
        and not i.get("upscaled")
    ]
    if not targets:
        raise HTTPException(status_code=400, detail="No completed items to upscale")

    client = ComfyClient.setup(cfg.COMFY_URL)
    upscaled_items = []

    for item in targets:
        chunk_number = item.get("chunk_number", "0")
        session_id = item.get("session_id", "")
        image_index = item.get("image_index", 0)
        wf = build_upscale(session_id, chunk_number, image_index=image_index)
        try:
            prompt_id = await client.queue_prompt(wf)
        except Exception as e:
            logger.error(
                f"Failed to queue upscale in session {req.session_id} for chunk {chunk_number}: {e}"
            )
            continue

        new_item = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "parent_id": item["id"],
            "db_name": item.get("db_name", ""),
            "segment_index": item.get("segment_index", 0),
            "prompt_resolved": f"[upscale] {item.get('prompt_resolved', '')}",
            "chunk_number": chunk_number,
            "prompt_id": prompt_id,
            "status": "pending",
            "filename": "",
            "upscaled": True,
            "image_index": image_index,
            "config": item.get("config", {}),
            "width": cfg.WIDTH * 4,
            "height": cfg.HEIGHT * 4,
        }
        upscaled_items.append(new_item)

    if upscaled_items:
        async with state_lock:
            st = load_state()
            st = upscaled_items + st
            save_state(st)

    return {"items": upscaled_items, "count": len(upscaled_items)}


@router.post("/api/sessions/{session_id}/retry-failed")
async def retry_failed_session_items(session_id: str):
    async with state_lock:
        st = load_state()

    # Find all items of this session that are failed
    failed_items = [
        item
        for item in st
        if item.get("session_id") == session_id and item.get("status") == "failed"
    ]
    if not failed_items:
        return {"status": "ok", "retried_count": 0, "message": "No failed items to retry"}

    client = ComfyClient.setup(cfg.COMFY_URL)
    retried_count = 0
    updates = {}

    for item in failed_items:
        seg_idx = item.get("segment_index")
        db_name = item.get("db_name", "default")

        # Load prompt template
        prompt_original = ""
        if seg_idx is not None:
            try:
                db = load_db(db_name)
                segs = sorted(db.get("segments", []), key=lambda s: s.get("index", 0))
                if seg_idx < len(segs):
                    prompt_original = segs[seg_idx]["text"]
            except Exception as e:
                logger.error(f"Failed to load segment {seg_idx} from db {db_name}: {e}")

        if not prompt_original:
            prompt_original = item.get("prompt_resolved", "")
            if prompt_original.startswith("positive:"):
                m = re.match(r"^positive:(.*?)(?:\nnegative:|$)", prompt_original, re.DOTALL)
                if m:
                    prompt_original = m.group(1)

        # Resolve prompt using item's config
        cfg_data = item.get("config", {})
        try:
            resolver_cfg = _resolve_generation_config(cfg_data, db_name)
            resolver = PromptResolver.setup(resolver_cfg)
            resolved_prompt = resolver.resolve_text(wrap_segment(prompt_original))
        except Exception as e:
            logger.error(f"Failed to resolve prompt: {e}")
            resolved_prompt = wrap_segment(prompt_original)

        edit_folder = f"retry_{str(uuid.uuid4())}"
        wf = build_batch(resolved_prompt, edit_folder, session_id)

        try:
            prompt_id = await client.queue_prompt(wf)
        except Exception as e:
            logger.error(f"Failed to queue retry prompt for item {item['id']}: {e}")
            continue

        item_id = item["id"]
        updates[item_id] = {
            "prompt_resolved": resolved_prompt,
            "prompt_id": prompt_id,
            "status": "pending",
            "filename": "",
            "chunk_number": edit_folder,
            "image_index": 0,
            "upscaled": False,
        }

        for d in [IMAGES_DIR, THUMBS_DIR]:
            for ext in [".png", ".jpg"]:
                p = d / f"{item_id}{ext}"
                p.unlink(missing_ok=True)

        retried_count += 1

    if updates:
        async with state_lock:
            st_latest = load_state()
            for item_latest in st_latest:
                item_latest_id = item_latest["id"]
                if item_latest_id in updates:
                    item_latest.update(updates[item_latest_id])
            save_state(st_latest)

    return {"status": "ok", "retried_count": retried_count}


@router.post("/api/sessions/{session_id}/delete")
async def delete_session(session_id: str):
    async with state_lock:
        st = load_state()
        session_items = [item for item in st if item.get("session_id") == session_id]
        if not session_items:
            return {"status": "ok", "deleted_count": 0}

        all_removed = {item["id"] for item in session_items}

        # Find the parent of the items we are deleting to reparent any children that are not in this session
        for item_id in all_removed:
            parent_id = None
            for item in st:
                if item["id"] == item_id:
                    parent_id = item.get("parent_id")
                    break

            # Reparent all immediate children of item_id to parent_id (if the child is not also being deleted)
            for item in st:
                if item.get("parent_id") == item_id and item["id"] not in all_removed:
                    item["parent_id"] = parent_id

        prompt_ids_to_cancel = {
            item.get("prompt_id") for item in session_items if item.get("prompt_id")
        }

        should_interrupt = False
        if prompt_ids_to_cancel:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    queue_data = await client.get(f"{cfg.COMFY_URL}/queue")
                    queue_json = queue_data.json()
                    running_ids = {q[1] for q in queue_json.get("queue_running", [])}
                    if prompt_ids_to_cancel & running_ids:
                        should_interrupt = True
            except Exception as e:
                logger.error(f"Failed to check running jobs: {e}")

        async with httpx.AsyncClient(timeout=5.0) as client:
            if should_interrupt:
                try:
                    await client.post(f"{cfg.COMFY_URL}/interrupt")
                except Exception as e:
                    logger.error(f"Failed to interrupt ComfyUI: {e}")
            for pid in prompt_ids_to_cancel:
                try:
                    await client.post(f"{cfg.COMFY_URL}/queue", json={"delete": [pid]})
                except Exception as e:
                    logger.error(f"Failed to delete job {pid} from ComfyUI queue: {e}")

        # Purge items
        new_st = [item for item in st if item["id"] not in all_removed]
        save_state(new_st)

    # Also remove session record from sessions store
    delete_session_record(session_id)

    for rid in all_removed:
        for d in [IMAGES_DIR, THUMBS_DIR]:
            for ext in [".png", ".jpg"]:
                p = d / f"{rid}{ext}"
                p.unlink(missing_ok=True)

    return {"status": "ok", "deleted_count": len(all_removed)}
