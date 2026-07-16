# Project guidelines

SceneQueue is a local-first FastAPI application for building ordered
prompt storyboards, queueing them in ComfyUI, and reflecting generated images in
its gallery.

## Architecture

- `server.py` owns application startup.
- `routes/api.py` owns the HTTP boundary.
- `src/core.py` owns paths, configuration, databases, and persisted state.
- `src/workflows.py` owns prompt resolution and ComfyUI workflow injection.
- `src/poller.py` owns ComfyUI queue and output synchronization.
- `frontend.html` and `static/` contain the dependency-light web client.
- `defaults/` contains immutable source defaults.
- `.data/` contains all mutable local state and is never committed.

## Rules

- Keep the core content-neutral. Domain-specific prompts belong in user data.
- Do not add machine-specific paths, model filenames, API keys, or generated
  images to source control.
- Treat workflow node mappings as configuration, not application constants.
- Change producers and consumers together; this project does not preserve
  compatibility with unpublished private data.
- Persist JSON atomically.
- Use `uv run pytest` and `uv run ruff check .` before committing.
