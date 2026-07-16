from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import httpx
import websockets

from . import core as cfg
from .core import (
    CLIENT_ID,
    IMAGES_DIR,
    THUMBS_DIR,
    active_progress,
    load_state,
    logger,
    save_state,
    state_lock,
)
from .workflows import generate_thumbnail


async def _save_image_from_comfy(
    target_id: str, comfy_file: Path, img_url: str, client: httpx.AsyncClient
) -> bool:
    local_path = IMAGES_DIR / f"{target_id}.png"
    local_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Tentativa via Unix Hard Link (Espaço zero, instantâneo)
    if comfy_file.exists():
        try:
            local_path.unlink(missing_ok=True)
            os.link(comfy_file, local_path)
            logger.info(f"Created Unix hard link from {comfy_file} to {local_path}")
            return True
        except OSError as e:
            logger.warning(f"Failed to create hard link, falling back to copy/move: {e}")
            try:
                shutil.copy(str(comfy_file), str(local_path))
                logger.info(f"Copied local image from {comfy_file} to {local_path}")
                return True
            except Exception as e_copy:
                logger.error(f"Failed to copy local image: {e_copy}")

    # 2. Fallback de rede (download via HTTP)
    if img_url:
        try:
            res = await client.get(img_url)
            if res.status_code == 200:
                with local_path.open("wb") as f:
                    f.write(res.content)
                logger.info(f"Downloaded image via HTTP to {local_path}")
                return True
        except Exception as e:
            logger.error(f"Failed to download image {img_url} to {local_path}: {e}")
    return False


def _clean_empty_parent_dirs(comfy_file: Path) -> None:
    try:
        chunk_dir = comfy_file.parent
        if chunk_dir.exists() and chunk_dir.is_dir() and chunk_dir != cfg.OUTPUT_DIR:
            if not any(chunk_dir.iterdir()):
                chunk_dir.rmdir()
                logger.info(f"Cleaned up empty ComfyUI output chunk directory: {chunk_dir}")

                session_dir = chunk_dir.parent
                if session_dir.exists() and session_dir.is_dir() and session_dir != cfg.OUTPUT_DIR:
                    if not any(session_dir.iterdir()):
                        session_dir.rmdir()
                        logger.info(
                            f"Cleaned up empty ComfyUI output session directory: {session_dir}"
                        )
    except Exception as e:
        logger.error(f"Failed to clean up empty directories: {e}")


def get_ws_url() -> str:
    url = cfg.COMFY_URL.rstrip("/")
    if url.startswith("https://"):
        return f"wss://{url[8:]}/ws?clientId={CLIENT_ID}"
    elif url.startswith("http://"):
        return f"ws://{url[7:]}/ws?clientId={CLIENT_ID}"
    else:
        return f"ws://{url}/ws?clientId={CLIENT_ID}"


async def handle_prompt_finished(pid: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await handle_prompt_completion_with_client(pid, client)
    except Exception as e:
        logger.error(f"Error in handle_prompt_finished for prompt {pid}: {e}")


async def handle_prompt_failure(pid: str) -> None:
    async with state_lock:
        st = load_state()
        updated = False
        for entry in st:
            if entry.get("prompt_id") == pid and entry.get("status") == "pending":
                entry["status"] = "failed"
                updated = True
        if updated:
            save_state(st)
            logger.info(f"Marked items for prompt {pid} as failed")


async def handle_node_executed(pid: str, images_list: list[dict]) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            async with state_lock:
                st = load_state()
            pending = [i for i in st if i.get("status") == "pending" and i.get("prompt_id") == pid]
            if not pending:
                return

            pid_groups = {item.get("image_index", 0): item["id"] for item in pending}
            state_updates = {}

            for arr_idx, img_data in enumerate(images_list):
                target_id = pid_groups.get(arr_idx)
                if not target_id:
                    continue
                img_url = f"{cfg.COMFY_URL}/view?filename={img_data['filename']}&subfolder={img_data['subfolder']}&type={img_data['type']}"
                comfy_file = cfg.OUTPUT_DIR / img_data["subfolder"] / img_data["filename"]

                if await _save_image_from_comfy(target_id, comfy_file, img_url, client):
                    local_path = IMAGES_DIR / f"{target_id}.png"
                    thumb_path = THUMBS_DIR / f"{target_id}.jpg"
                    generate_thumbnail(local_path, thumb_path)
                    state_updates[target_id] = str(local_path)

            if state_updates:
                async with state_lock:
                    st = load_state()
                    for entry in st:
                        if entry["id"] in state_updates:
                            entry["status"] = "completed"
                            entry["filename"] = state_updates[entry["id"]]
                    save_state(st)
    except Exception as e:
        logger.error(f"Error in handle_node_executed for prompt {pid}: {e}")


async def check_filesystem_fallback_with_client(
    pid: str, pending: list[dict], client: httpx.AsyncClient
) -> bool:
    any_found = False
    comfy_files_to_clean = []

    sid = pending[0].get("session_id", "")
    pnum = pending[0].get("chunk_number", "0")

    if sid:
        fs_dir = cfg.OUTPUT_DIR / sid / pnum
        try:
            if fs_dir.exists():
                fs_images = sorted(
                    [p for p in fs_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".webp")],
                    key=lambda p: p.name,
                )
                state_updates = {}
                for item in pending:
                    img_idx = item.get("image_index", 0)
                    target_id = item["id"]
                    if img_idx < len(fs_images):
                        any_found = True
                        fs_img = fs_images[img_idx]
                        comfy_files_to_clean.append(fs_img)

                        if await _save_image_from_comfy(target_id, fs_img, "", client):
                            local_path = IMAGES_DIR / f"{target_id}.png"
                            thumb_path = THUMBS_DIR / f"{target_id}.jpg"
                            generate_thumbnail(local_path, thumb_path)
                            state_updates[target_id] = str(local_path)

                if state_updates:
                    async with state_lock:
                        st = load_state()
                        for entry in st:
                            if entry["id"] in state_updates:
                                entry["status"] = "completed"
                                entry["filename"] = state_updates[entry["id"]]
                        save_state(st)
        except Exception as e:
            logger.error(f"Failed to load images from directory {fs_dir} for prompt {pid}: {e}")

    for cf in comfy_files_to_clean:
        _clean_empty_parent_dirs(cf)

    return any_found


async def handle_prompt_completion_with_client(pid: str, client: httpx.AsyncClient) -> bool:
    async with state_lock:
        st = load_state()
    pending = [i for i in st if i.get("status") == "pending" and i.get("prompt_id") == pid]
    if not pending:
        return True

    try:
        resp = await client.get(f"{cfg.COMFY_URL}/history/{pid}")
        if resp.status_code != 200:
            logger.error(f"Failed to fetch history for completed prompt {pid}: {resp.status_code}")
            return False
        history_data = resp.json()
    except Exception as e:
        logger.error(f"Failed to query history for prompt {pid}: {e}")
        return False

    if pid not in history_data:
        return await check_filesystem_fallback_with_client(pid, pending, client)

    outputs = history_data[pid].get("outputs", {})
    images_found = None
    for node_id in outputs:
        if "images" in outputs[node_id]:
            images_found = outputs[node_id]["images"]
            break

    if images_found:
        pid_groups = {item.get("image_index", 0): item["id"] for item in pending}
        comfy_files_to_clean = []
        state_updates = {}

        for arr_idx, img_data in enumerate(images_found):
            target_id = pid_groups.get(arr_idx)
            if not target_id:
                continue
            img_url = f"{cfg.COMFY_URL}/view?filename={img_data['filename']}&subfolder={img_data['subfolder']}&type={img_data['type']}"
            comfy_file = cfg.OUTPUT_DIR / img_data["subfolder"] / img_data["filename"]
            comfy_files_to_clean.append(comfy_file)

            if await _save_image_from_comfy(target_id, comfy_file, img_url, client):
                local_path = IMAGES_DIR / f"{target_id}.png"
                thumb_path = THUMBS_DIR / f"{target_id}.jpg"
                generate_thumbnail(local_path, thumb_path)
                state_updates[target_id] = str(local_path)

        if state_updates:
            async with state_lock:
                st = load_state()
                for entry in st:
                    if entry["id"] in state_updates:
                        entry["status"] = "completed"
                        entry["filename"] = state_updates[entry["id"]]
                save_state(st)
                logger.info(f"Updated {len(state_updates)} items to completed for prompt {pid}")

        for cf in comfy_files_to_clean:
            _clean_empty_parent_dirs(cf)
        return True
    else:
        return await check_filesystem_fallback_with_client(pid, pending, client)


async def sync_pending_items() -> None:
    try:
        async with state_lock:
            st = load_state()
        pending = [i for i in st if i.get("status") == "pending" and i.get("prompt_id")]
        if not pending:
            return

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                queue_resp = await client.get(f"{cfg.COMFY_URL}/queue")
                if queue_resp.status_code == 200:
                    queue_data = queue_resp.json()
                    running_ids = {q[1] for q in queue_data.get("queue_running", [])}
                    pending_q_ids = {q[1] for q in queue_data.get("queue_pending", [])}
                    active_ids = running_ids | pending_q_ids
                else:
                    active_ids = set()
            except Exception as e:
                logger.error(f"Failed to fetch queue in sync_pending_items: {e}")
                active_ids = set()

            pid_groups = {}
            for item in pending:
                pid = item["prompt_id"]
                if pid not in pid_groups:
                    pid_groups[pid] = []
                pid_groups[pid].append(item)

            for pid, items in pid_groups.items():
                if pid in active_ids:
                    continue

                success = await handle_prompt_completion_with_client(pid, client)
                if not success:
                    await handle_prompt_failure(pid)
    except Exception as e:
        logger.error(f"Error in sync_pending_items: {e}", exc_info=True)


async def websocket_listener() -> None:
    ws_url = get_ws_url()
    backoff = 1.0
    while True:
        try:
            logger.info(f"Connecting to ComfyUI WebSocket at {ws_url}...")
            async with websockets.connect(ws_url) as ws:
                logger.info("Connected to ComfyUI WebSocket.")
                backoff = 1.0

                await sync_pending_items()

                async for message in ws:
                    if isinstance(message, bytes):
                        continue

                    try:
                        event = json.loads(message)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")
                    data = event.get("data", {})

                    if etype == "execution_start":
                        pid = data.get("prompt_id")
                        if pid:
                            active_progress[pid] = {"progress": 0.0, "node": None}
                    elif etype == "progress":
                        pid = data.get("prompt_id")
                        val = data.get("value", 0)
                        mx = data.get("max", 1)
                        node = data.get("node")
                        if pid:
                            active_progress[pid] = {
                                "progress": val / mx if mx > 0 else 0.0,
                                "node": node,
                            }
                    elif etype == "executing":
                        pid = data.get("prompt_id")
                        node = data.get("node")
                        if pid:
                            if node is None:
                                active_progress.pop(pid, None)
                                asyncio.create_task(handle_prompt_finished(pid))
                            else:
                                if pid not in active_progress:
                                    active_progress[pid] = {"progress": 0.0, "node": node}
                                else:
                                    active_progress[pid]["node"] = node
                    elif etype == "executed":
                        pid = data.get("prompt_id")
                        output = data.get("output", {})
                        if pid and "images" in output:
                            asyncio.create_task(handle_node_executed(pid, output["images"]))
                    elif etype == "execution_error":
                        pid = data.get("prompt_id")
                        if pid:
                            active_progress.pop(pid, None)
                            await handle_prompt_failure(pid)
        except (
            websockets.exceptions.ConnectionClosed,
            ConnectionRefusedError,
            OSError,
            Exception,
        ) as e:
            logger.warning(f"WebSocket connection error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def poll_comfy() -> None:
    ws_task = asyncio.create_task(websocket_listener())
    try:
        while True:
            try:
                await sync_pending_items()
            except Exception as e:
                logger.error(f"Unexpected error in sync_pending_items loop: {e}", exc_info=True)
            await asyncio.sleep(10.0)
    finally:
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
