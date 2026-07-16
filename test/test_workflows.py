from pathlib import Path
from unittest.mock import patch

from src import core as cfg
from src.workflows import build_batch, build_upscale


@patch("src.workflows._load_template")
def test_build_batch_uses_configured_prompt_node(mock_load) -> None:
    mock_load.return_value = {
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512},
        },
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "image"}},
    }

    with (
        patch.object(cfg, "TARGET_NODE_ID", "2"),
        patch.object(cfg, "TARGET_INPUT_KEY", "text"),
        patch.object(cfg, "WIDTH", 768),
        patch.object(cfg, "HEIGHT", 1024),
        patch.object(cfg, "CHECKPOINT", ""),
        patch.object(cfg, "LORAS", []),
    ):
        workflow = build_batch("test prompt", "1", "session-123")

    assert workflow["2"]["inputs"]["text"] == "test prompt"
    assert workflow["4"]["inputs"]["width"] == 768
    assert workflow["4"]["inputs"]["height"] == 1024
    assert workflow["7"]["inputs"]["filename_prefix"] == "session-123/1/image"


@patch("src.workflows._load_template")
def test_build_upscale_stages_source_image(mock_load, tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    source_dir = output_dir / "session-123" / "1"
    source_dir.mkdir(parents=True)
    (source_dir / "000.png").write_bytes(b"image")

    mock_load.return_value = {
        "1": {"class_type": "LoadImage", "inputs": {"image": ""}},
        "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "upscaled"}},
    }

    with (
        patch.object(cfg, "OUTPUT_DIR", output_dir),
        patch.object(cfg, "COMFY_ROOT", tmp_path),
        patch.object(cfg, "WIDTH", 768),
        patch.object(cfg, "HEIGHT", 1024),
    ):
        workflow = build_upscale("session-123", "1", image_index=0)

    staged = tmp_path / "input" / workflow["1"]["inputs"]["image"]
    assert staged.exists()
    assert workflow["4"]["inputs"]["filename_prefix"] == ("session-123/upscaled/1_0/upscaled")
