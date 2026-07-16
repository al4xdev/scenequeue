from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routes.api import router as api_router
from src.core import ROOT, ensure_dirs, logger, migrate_sessions_from_state
from src.poller import poll_comfy


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_dirs()
    n = migrate_sessions_from_state()
    if n:
        logger.info(f"Migrated {n} session(s) from state.json to sessions.json")
    task = asyncio.create_task(poll_comfy())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="SceneQueue",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=os.getenv("SCENEQUEUE_HOST", "127.0.0.1"),
        port=int(os.getenv("SCENEQUEUE_PORT", "8889")),
        reload=False,
    )
