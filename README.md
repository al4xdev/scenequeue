# SceneQueue

SceneQueue is a local-first storyboard and batch generation interface
for ComfyUI. It keeps prompts as ordered frames, queues them through a configurable
API workflow, tracks progress, and reflects generated images in a session gallery.

The repository ships with general-purpose, safe-for-work examples and a workflow
made only from standard ComfyUI nodes. Your prompts, generated images, selected
models, and local workflow edits live under `.data/` and are never committed.

## Features

- Ordered prompt databases with insert, edit, history, and retry operations.
- Reusable placeholders for subject, appearance, wardrobe, pose, scene, and style.
- Batch generation with configurable resolution, checkpoint, LoRAs, and chunk size.
- ComfyUI queue monitoring over WebSocket with HTTP fallback.
- Session gallery, thumbnails, failed-slot recovery, and optional upscaling.
- Optional OpenRouter assistant for prompt editing and transition-frame generation.
- Installable PWA frontend with no JavaScript build step.

## Requirements

- Python 3.11 or newer.
- [`uv`](https://docs.astral.sh/uv/).
- A running ComfyUI instance reachable from this machine.

The default generation workflow uses these standard nodes:
`CheckpointLoaderSimple`, `CLIPTextEncode`, `EmptyLatentImage`, `KSampler`,
`VAEDecode`, and `SaveImage`.

## Quickstart

```bash
git clone https://github.com/your-name/scenequeue.git
cd scenequeue
./install.sh
./start.sh
```

Open <http://127.0.0.1:8889>, then use Settings to configure:

- the ComfyUI URL;
- the local ComfyUI root directory;
- a checkpoint;
- the prompt node ID and input key if you replace the bundled workflow;
- generation resolution, prompts per batch, and optional LoRAs.

The first run copies source defaults into `.data/`. Editing prompt databases or
workflows through the application never changes the files committed in `defaults/`.

## Included manga demo

Select the `manga_demo` database in each prompt category to queue an eight-frame
story called **The Last Delivery** with its intended courier, wardrobe, station,
and manga style. The sequence demonstrates:

- stable subject, wardrobe, prop, and location continuity;
- establishing, reaction, reveal, action, and closing shots;
- deliberate pacing across a complete visual micro-story;
- reusable placeholders, so the same sequence can feature a person, robot, fox,
  or any subject added by the user.

### Docker

```bash
docker build -t scenequeue .
docker run --rm -p 8889:8889 -v scenequeue-data:/app/.data scenequeue
```

When ComfyUI runs on the host, set its reachable URL in Settings. On Linux,
host networking may be the simplest option for a local-only setup.

## Custom workflows

Export a workflow in ComfyUI's API format and replace:

- `.data/workflows/workflow_api.json` for generation;
- `.data/workflows/upscale_api.json` for upscaling.

Set `target_node_id` and `target_input_key` in Settings to the node/input that
receives the positive prompt. Checkpoint and LoRA injection is discovered by node
class, so users normally only need to choose another model in the interface.

The bundled upscale workflow expects `RealESRGAN_x4plus.pth`. Change its
`UpscaleModelLoader` value if that model is not installed.

## Optional OpenRouter assistant

Create `.data/.env`:

```env
OPENROUTER_API_KEY=your-key
OPENROUTER_MODELS=deepseek/deepseek-chat,meta-llama/llama-3.3-70b-instruct
```

The API key remains server-side and is never returned by the settings endpoint.
Without it, every non-AI editing and generation feature remains available.

## Data layout

```text
.data/
├── config.json
├── databases/
├── gallery/
└── workflows/
```

Set `SCENEQUEUE_DATA_DIR` to store runtime data elsewhere. Set
`SCENEQUEUE_HOST` and `SCENEQUEUE_PORT` to change the default
`127.0.0.1:8889` listener.

This application has no authentication layer. Keep it bound to localhost or a
trusted private network.

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
```

GitHub Actions runs the same lint and test checks without requiring a live ComfyUI
server or OpenRouter account.

## License

MIT
