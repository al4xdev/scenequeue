from pathlib import Path
from unittest.mock import patch

from src import core as cfg
from src.workflows import build_batch, build_upscale


@patch("src.workflows._load_template")
def test_build_batch_uses_configured_prompt_node(mock_load) -> None:
    mock_load.return_value = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        },
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "bad quality"},
            "_meta": {"title": "Negative prompt"},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {},
            "_meta": {"title": "Sampler"},
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0]}},
        "7": {
            "class_type": "LatentUpscale",
            "inputs": {"width": 768, "height": 1024},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {},
            "_meta": {"title": "High-resolution sampler"},
        },
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "image"}},
    }

    with (
        patch.object(cfg, "TARGET_NODE_ID", "2"),
        patch.object(cfg, "TARGET_INPUT_KEY", "text"),
        patch.object(cfg, "WIDTH", 768),
        patch.object(cfg, "HEIGHT", 1024),
        patch.object(cfg, "CHECKPOINT", "model.safetensors"),
        patch.object(cfg, "LORAS", []),
        patch.object(cfg, "SAMPLER_NAME", "dpmpp_2m_sde_heun_gpu"),
        patch.object(cfg, "SCHEDULER", "beta57"),
        patch.object(cfg, "STEPS", 12),
        patch.object(cfg, "CFG_SCALE", 1.0),
        patch.object(cfg, "DENOISE", 1.0),
        patch.object(cfg, "HIGHRES_ENABLED", True),
        patch.object(cfg, "HIGHRES_SCALE", 1.5),
        patch.object(cfg, "HIGHRES_STEPS", 4),
        patch.object(cfg, "HIGHRES_CFG_SCALE", 1.6),
        patch.object(cfg, "HIGHRES_DENOISE", 0.45),
        patch.object(cfg, "ADULT_CONTENT", False),
    ):
        workflow = build_batch("test prompt", "1", "session-123")

    assert workflow["2"]["inputs"]["text"] == "test prompt"
    assert workflow["4"]["inputs"]["width"] == 768
    assert workflow["4"]["inputs"]["height"] == 1024
    assert workflow["5"]["inputs"]["steps"] == 12
    assert workflow["5"]["inputs"]["scheduler"] == "beta57"
    assert workflow["7"]["inputs"]["width"] == 1152
    assert workflow["7"]["inputs"]["height"] == 1536
    assert workflow["8"]["inputs"]["denoise"] == 0.45
    assert workflow["6"]["inputs"]["samples"] == ["8", 0]
    assert "nsfw" in workflow["3"]["inputs"]["text"]
    assert workflow["9"]["inputs"]["filename_prefix"] == "session-123/1/image"


@patch("src.workflows._load_template")
def test_build_batch_can_disable_highres_and_allow_adult_content(mock_load) -> None:
    mock_load.return_value = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        },
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "bad quality"},
            "_meta": {"title": "Negative prompt"},
        },
        "5": {"class_type": "KSampler", "inputs": {}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0]}},
        "8": {"class_type": "KSampler", "inputs": {}},
    }

    with (
        patch.object(cfg, "CHECKPOINT", "model.safetensors"),
        patch.object(cfg, "LORAS", []),
        patch.object(cfg, "SAMPLER_NAME", "euler"),
        patch.object(cfg, "SCHEDULER", "simple"),
        patch.object(cfg, "STEPS", 10),
        patch.object(cfg, "CFG_SCALE", 2.0),
        patch.object(cfg, "DENOISE", 1.0),
        patch.object(cfg, "HIGHRES_ENABLED", False),
        patch.object(cfg, "HIGHRES_SCALE", 1.5),
        patch.object(cfg, "HIGHRES_STEPS", 4),
        patch.object(cfg, "HIGHRES_CFG_SCALE", 1.6),
        patch.object(cfg, "HIGHRES_DENOISE", 0.45),
        patch.object(cfg, "ADULT_CONTENT", True),
        patch.object(cfg, "TARGET_NODE_ID", "2"),
        patch.object(cfg, "TARGET_INPUT_KEY", "text"),
    ):
        workflow = build_batch("test prompt", "1", "session-123")

    assert workflow["6"]["inputs"]["samples"] == ["5", 0]
    assert workflow["3"]["inputs"]["text"] == "bad quality"


@patch("src.workflows._load_template")
def test_build_batch_inserts_configured_lora_into_standard_workflow(mock_load) -> None:
    mock_load.return_value = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["1", 1]},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {"model": ["1", 0]},
        },
    }

    with (
        patch.object(cfg, "CHECKPOINT", "model.safetensors"),
        patch.object(
            cfg,
            "LORAS",
            [
                {
                    "name": "style.safetensors",
                    "strength_model": 0.8,
                    "strength_clip": 0.6,
                }
            ],
        ),
        patch.object(cfg, "SAMPLER_NAME", "euler"),
        patch.object(cfg, "SCHEDULER", "simple"),
        patch.object(cfg, "STEPS", 10),
        patch.object(cfg, "CFG_SCALE", 2.0),
        patch.object(cfg, "DENOISE", 1.0),
        patch.object(cfg, "HIGHRES_ENABLED", False),
        patch.object(cfg, "HIGHRES_SCALE", 1.5),
        patch.object(cfg, "HIGHRES_STEPS", 4),
        patch.object(cfg, "HIGHRES_CFG_SCALE", 1.6),
        patch.object(cfg, "HIGHRES_DENOISE", 0.45),
        patch.object(cfg, "ADULT_CONTENT", True),
        patch.object(cfg, "TARGET_NODE_ID", "2"),
        patch.object(cfg, "TARGET_INPUT_KEY", "text"),
    ):
        workflow = build_batch("test prompt", "1", "session-123")

    lora = workflow["6"]
    assert lora["class_type"] == "LoraLoader"
    assert lora["inputs"]["lora_name"] == "style.safetensors"
    assert lora["inputs"]["strength_model"] == 0.8
    assert workflow["2"]["inputs"]["clip"] == ["6", 1]
    assert workflow["5"]["inputs"]["model"] == ["6", 0]


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
