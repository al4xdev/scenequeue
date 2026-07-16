from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

JsonDict = dict[str, Any]
State = list[JsonDict]

# =====================================================================
# 1. Paths & Directories
# =====================================================================
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SCENEQUEUE_DATA_DIR", ROOT / ".data")).expanduser()
DEFAULTS_DIR = ROOT / "defaults"

# Gallery / state
GALLERY_DIR = DATA_DIR / "gallery"
IMAGES_DIR = GALLERY_DIR / "images"
THUMBS_DIR = GALLERY_DIR / "thumbnails"
STATE_FILE = GALLERY_DIR / "state.json"
LOG_FILE = GALLERY_DIR / "scenequeue.log"

# Databases
DATABASES_DIR = DATA_DIR / "databases"
ACTIVE_DB_DIR = DATABASES_DIR / ".active"

# Workflows
WORKFLOWS_DIR = DATA_DIR / "workflows"
WORKFLOW_PATH = WORKFLOWS_DIR / "workflow_api.json"
UPSCALE_WORKFLOW_PATH = WORKFLOWS_DIR / "upscale_api.json"

# Config
CONFIG_FILE = DATA_DIR / "config.json"

# Frontend
FRONTEND_PATH = ROOT / "frontend.html"


DB_TYPES = ["prompts", "subjects", "appearances", "wardrobes", "poses", "scenes", "styles"]


def ensure_dirs() -> None:
    for d in [DATA_DIR, GALLERY_DIR, IMAGES_DIR, THUMBS_DIR, DATABASES_DIR, WORKFLOWS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    for t in DB_TYPES:
        (DATABASES_DIR / t).mkdir(parents=True, exist_ok=True)

    for source in (DEFAULTS_DIR / "databases").glob("*/*.json"):
        relative = source.relative_to(DEFAULTS_DIR / "databases")
        destination = DATABASES_DIR / relative
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    for source in (DEFAULTS_DIR / "workflows").glob("*.json"):
        destination = WORKFLOWS_DIR / source.name
        if not destination.exists():
            shutil.copy2(source, destination)


# =====================================================================
# 2. Config Loading & Saving
# =====================================================================
def _default_config() -> JsonDict:
    return {
        "comfy_url": os.getenv("COMFY_URL", "http://127.0.0.1:8188"),
        "target_node_id": os.getenv("TARGET_NODE_ID", "2"),
        "target_input_key": os.getenv("TARGET_INPUT_KEY", "text"),
        "width": int(os.getenv("WIDTH", "768")),
        "height": int(os.getenv("HEIGHT", "1024")),
        "comfy_root": os.getenv("COMFY_ROOT", ""),
        "checkpoint": "",
        "loras": [],
        "chunk_size": 1,
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
        "adult_content": False,
    }


def load_config() -> JsonDict:
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                raise ValueError("config.json must contain a JSON object")
            cfg = _default_config()
            cfg.update(saved)
            return cfg
        except Exception as e:
            logging.getLogger("scenequeue").error(
                f"Failed to load config.json, using defaults: {e}"
            )
    return _default_config()


def save_config(cfg: JsonDict) -> None:
    _atomic_write_json(CONFIG_FILE, cfg)


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_env_variables() -> dict[str, str]:
    vars_dict = {}
    # 1. Check local .env
    local_env = DATA_DIR / ".env"
    if local_env.exists():
        vars_dict.update(_parse_env_file(local_env))
    # 2. Allow system environment variables to override
    for k in ["OPENROUTER_API_KEY", "OPENROUTER_MODELS"]:
        val = os.getenv(k)
        if val:
            vars_dict[k] = val
    return vars_dict


def save_openrouter_settings(
    api_key: str | None,
    models: list[str],
    *,
    clear_key: bool = False,
) -> None:
    local_env = DATA_DIR / ".env"
    current = _parse_env_file(local_env) if local_env.exists() else {}

    if clear_key:
        current.pop("OPENROUTER_API_KEY", None)
    elif api_key:
        clean_key = api_key.strip()
        if "\n" in clean_key or "\r" in clean_key:
            raise ValueError("OpenRouter API key cannot contain line breaks.")
        current["OPENROUTER_API_KEY"] = clean_key

    clean_models = [model.strip() for model in models if model.strip()]
    if clean_models:
        if any("\n" in model or "\r" in model for model in clean_models):
            raise ValueError("OpenRouter model names cannot contain line breaks.")
        current["OPENROUTER_MODELS"] = ",".join(clean_models)
    else:
        current.pop("OPENROUTER_MODELS", None)

    ordered_keys = ["OPENROUTER_API_KEY", "OPENROUTER_MODELS"]
    content = "".join(f"{key}={current[key]}\n" for key in ordered_keys if current.get(key))
    if content:
        _atomic_write_text(local_env, content)
    else:
        local_env.unlink(missing_ok=True)


def _parse_env_file(path: Path) -> dict[str, str]:
    res = {}
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    res[k] = v
    except Exception as e:
        logging.getLogger("scenequeue").error(f"Failed to parse env file {path}: {e}")
    return res


_DEFAULT_OPENROUTER_MODELS = [
    "deepseek/deepseek-chat",
    "meta-llama/llama-3.3-70b-instruct",
]

COMFY_URL = "http://127.0.0.1:8188"
TARGET_NODE_ID = "2"
TARGET_INPUT_KEY = "text"
WIDTH = 768
HEIGHT = 1024
COMFY_ROOT = Path()
OUTPUT_DIR = Path()
CHECKPOINT = ""
LORAS: list[JsonDict] = []
OPENROUTER_API_KEY = ""
OPENROUTER_MODELS = _DEFAULT_OPENROUTER_MODELS
CHUNK_SIZE = 1
SAMPLER_NAME = "dpmpp_2m_sde_heun_gpu"
SCHEDULER = "beta57"
STEPS = 12
CFG_SCALE = 1.0
DENOISE = 1.0
HIGHRES_ENABLED = True
HIGHRES_SCALE = 1.5
HIGHRES_STEPS = 4
HIGHRES_CFG_SCALE = 1.6
HIGHRES_DENOISE = 0.45
ADULT_CONTENT = False


def reload_config() -> None:
    global \
        COMFY_URL, \
        TARGET_NODE_ID, \
        TARGET_INPUT_KEY, \
        WIDTH, \
        HEIGHT, \
        COMFY_ROOT, \
        OUTPUT_DIR, \
        CHECKPOINT, \
        LORAS
    global OPENROUTER_API_KEY, OPENROUTER_MODELS, CHUNK_SIZE
    global SAMPLER_NAME, SCHEDULER, STEPS, CFG_SCALE, DENOISE
    global HIGHRES_ENABLED, HIGHRES_SCALE, HIGHRES_STEPS, HIGHRES_CFG_SCALE, HIGHRES_DENOISE
    global ADULT_CONTENT

    _config = load_config()
    COMFY_URL = _config.get("comfy_url", "http://127.0.0.1:8188")
    TARGET_NODE_ID = str(_config.get("target_node_id", "2"))
    TARGET_INPUT_KEY = str(_config.get("target_input_key", "text"))
    WIDTH = int(_config.get("width", 768))
    HEIGHT = int(_config.get("height", 1024))
    COMFY_ROOT = Path(_config.get("comfy_root", ""))
    OUTPUT_DIR = COMFY_ROOT / "output"
    CHECKPOINT = _config.get("checkpoint", "")
    LORAS = _config.get("loras", [])
    CHUNK_SIZE = int(_config.get("chunk_size", 1))
    SAMPLER_NAME = str(_config.get("sampler_name", "dpmpp_2m_sde_heun_gpu"))
    SCHEDULER = str(_config.get("scheduler", "beta57"))
    STEPS = int(_config.get("steps", 12))
    CFG_SCALE = float(_config.get("cfg_scale", 1.0))
    DENOISE = float(_config.get("denoise", 1.0))
    HIGHRES_ENABLED = bool(_config.get("highres_enabled", True))
    HIGHRES_SCALE = float(_config.get("highres_scale", 1.5))
    HIGHRES_STEPS = int(_config.get("highres_steps", 4))
    HIGHRES_CFG_SCALE = float(_config.get("highres_cfg_scale", 1.6))
    HIGHRES_DENOISE = float(_config.get("highres_denoise", 0.45))
    ADULT_CONTENT = bool(_config.get("adult_content", False))

    _env_vars = load_env_variables()
    OPENROUTER_API_KEY = _env_vars.get("OPENROUTER_API_KEY", "")
    _models_raw = _env_vars.get("OPENROUTER_MODELS", "")
    if _models_raw:
        try:
            if _models_raw.startswith("["):
                OPENROUTER_MODELS = json.loads(_models_raw)
            else:
                OPENROUTER_MODELS = [m.strip() for m in _models_raw.split(",") if m.strip()]
        except Exception as e:
            logging.getLogger("scenequeue").error(
                f"Failed to parse OPENROUTER_MODELS, using defaults: {e}"
            )
            OPENROUTER_MODELS = _DEFAULT_OPENROUTER_MODELS
    else:
        OPENROUTER_MODELS = _DEFAULT_OPENROUTER_MODELS


# Initial load
reload_config()


# =====================================================================
# 3. Logging System
# =====================================================================
class Log:
    def __init__(self) -> None:
        self._logger: logging.Logger | None = None
        self._count: int = 0

    @classmethod
    def config(cls, log_file: Path, tool: str = "scenequeue", level: int = logging.INFO) -> Log:
        instance = cls()
        logger = logging.getLogger(tool)
        logger.setLevel(level)

        if logger.handlers:
            logger.handlers.clear()

        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] (%(name)s): %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        instance._logger = logger
        return instance

    def _write(self, level: str, msg: str, *args: Any, **kwargs: Any) -> Log:
        if self._logger is not None:
            self._count += 1
            getattr(self._logger, level)(f"[{self._count}] {msg}", *args, **kwargs)
        return self

    def info(self, msg: str, *args: Any, **kwargs: Any) -> Log:
        return self._write("info", msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> Log:
        return self._write("warning", msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> Log:
        return self._write("error", msg, *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> Log:
        return self._write("debug", msg, *args, **kwargs)


logger = Log.config(LOG_FILE, "scenequeue")

CLIENT_ID = str(uuid.uuid4())
active_progress: dict[str, dict[str, Any]] = {}


# =====================================================================
# 4. State Management (state.json)
# =====================================================================
# WARNING: state_lock is a module-level asyncio.Lock.
# This works correctly for single-process ASGI servers (workers=1).
# If running with multiple uvicorn processes/workers, this lock will not
# prevent process race conditions on the state file.
state_lock = asyncio.Lock()


def load_state() -> State:
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                value = json.load(f)
                return value if isinstance(value, list) else []
        except Exception as e:
            logger.error(f"Failed to load state.json: {e}")
            return []
    return []


def save_state(state: State) -> None:
    _atomic_write_json(STATE_FILE, state)


# =====================================================================
# 5. Database Management (databases/*.json)
# =====================================================================
def get_active_db_name(db_type: str = "prompts") -> str:
    active_file = ACTIVE_DB_DIR / db_type
    if active_file.exists():
        name = active_file.read_text().strip()
        if name and (DATABASES_DIR / db_type / f"{name}.json").exists():
            return name
    dbs = sorted((DATABASES_DIR / db_type).glob("*.json"))
    if dbs:
        return dbs[0].stem
    return "default"


def set_active_db_name(db_type: str, name: str) -> None:
    ACTIVE_DB_DIR.mkdir(parents=True, exist_ok=True)
    active_file = ACTIVE_DB_DIR / db_type
    fd, temporary_name = tempfile.mkstemp(dir=ACTIVE_DB_DIR, prefix=f".{db_type}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(name.strip())
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(active_file)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def get_active_db_path(db_type: str = "prompts") -> Path:
    return DATABASES_DIR / db_type / f"{get_active_db_name(db_type)}.json"


@lru_cache(maxsize=32)
def _load_db_cached(name: str, db_type: str) -> JsonDict:
    path = DATABASES_DIR / db_type / f"{name}.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            value = json.load(f)
            return value if isinstance(value, dict) else {"version": 2, "segments": []}
    return {"version": 2, "segments": []}


def load_db(name: str | None = None, db_type: str = "prompts") -> JsonDict:
    if name is None:
        name = get_active_db_name(db_type)
    return copy.deepcopy(_load_db_cached(name, db_type))


@lru_cache(maxsize=16)
def _get_db_lookup_cached(db_type: str, db_name: str) -> dict[str, str]:
    db = load_db(name=db_name, db_type=db_type)
    lookup: dict[str, str] = {}
    if db_type != "subjects":
        lookup["NONE"] = ""
    for s in sorted(db.get("segments", []), key=lambda x: x.get("index", 0)):
        text = s.get("text", "").strip()
        if not text:
            continue
        val = text
        if val.upper() == "NONE" or val == "":
            lookup["NONE"] = ""
        else:
            first_part = val.split(",")[0].strip()
            name = first_part.replace("_", " ").replace("-", " ").upper()
            if not name:
                name = val.upper()

            if name in lookup:
                counter = 2
                base_name = name
                while f"{base_name} {counter}" in lookup:
                    counter += 1
                name = f"{base_name} {counter}"

            lookup[name] = val
    return lookup


def get_db_lookup(db_type: str, db_name: str) -> dict[str, str]:
    """Cached {name: value} lookup for fast enum resolution."""
    return copy.copy(_get_db_lookup_cached(db_type, db_name))


def clear_db_lookup_cache() -> None:
    _get_db_lookup_cached.cache_clear()


def save_db(db: JsonDict, name: str | None = None, db_type: str = "prompts") -> None:
    if name:
        path = DATABASES_DIR / db_type / f"{name}.json"
    else:
        path = get_active_db_path(db_type)
    db["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(path, db)
    _load_db_cached.cache_clear()
    _get_db_lookup_cached.cache_clear()


def get_all_segments(db_type: str = "prompts") -> State:
    db = load_db(db_type=db_type)
    return sorted(db.get("segments", []), key=lambda s: s.get("index", 0))


# =====================================================================
# 6. Session Management (sessions.json)
# =====================================================================
SESSIONS_FILE = GALLERY_DIR / "sessions.json"


def load_sessions() -> State:
    """Load the explicit sessions list from sessions.json."""
    if SESSIONS_FILE.exists():
        try:
            with SESSIONS_FILE.open("r", encoding="utf-8") as f:
                value = json.load(f)
                return value if isinstance(value, list) else []
        except Exception as e:
            logger.error(f"Failed to load sessions.json: {e}")
            return []
    return []


def save_sessions(sessions: State) -> None:
    """Persist the sessions list to sessions.json."""
    _atomic_write_json(SESSIONS_FILE, sessions)


def get_session(session_id: str) -> JsonDict | None:
    """Retrieve a single session by ID, or None if not found."""
    sessions = load_sessions()
    return next((s for s in sessions if s["id"] == session_id), None)


def upsert_session(session_id: str, db_name: str, config: JsonDict) -> JsonDict:
    """Create or update a session. Returns the session record."""
    sessions = load_sessions()
    existing = next((s for s in sessions if s["id"] == session_id), None)
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        existing["db_name"] = db_name
        existing["subject_config"] = config
        save_sessions(sessions)
        return existing
    new_session = {
        "id": session_id,
        "db_name": db_name,
        "subject_config": config,
        "created_at": now,
    }
    sessions.append(new_session)
    save_sessions(sessions)
    return new_session


def delete_session_record(session_id: str) -> bool:
    """Remove a session record by ID. Returns True if removed."""
    sessions = load_sessions()
    before = len(sessions)
    sessions[:] = [s for s in sessions if s["id"] != session_id]
    if len(sessions) < before:
        save_sessions(sessions)
        return True
    return False


def migrate_sessions_from_state() -> int:
    """Migrate existing sessions inferred from state.json into sessions.json.

    Scans every item in the current state and collects unique session_id
    entries with their db_name and config.  Idempotent — existing session
    records are not overwritten (first-write-wins).
    """
    st = load_state()
    discovered: dict[str, JsonDict] = {}
    for item in st:
        sid = item.get("session_id")
        if not sid:
            continue
        if sid not in discovered:
            discovered[sid] = {
                "id": sid,
                "db_name": item.get("db_name", "default"),
                "subject_config": item.get("config", {}),
                "created_at": item.get("created_at", datetime.now(timezone.utc).isoformat()),
            }

    if not discovered:
        return 0

    existing = load_sessions()
    existing_ids = {s["id"] for s in existing}
    for s in discovered.values():
        if s["id"] not in existing_ids:
            existing.append(s)
    save_sessions(existing)
    return len(discovered)
