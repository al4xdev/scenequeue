from pathlib import Path
from unittest.mock import MagicMock, patch

from src.enums import Appearance, GenerationConfig, Pose, Scene, Style, Subject, Wardrobe
from src.workflows import PromptResolver


def make_config(**overrides: str) -> GenerationConfig:
    values = {
        "subject": Subject.PERSON,
        "appearance": Appearance.CASUAL,
        "wardrobe": Wardrobe.CASUAL,
        "pose": Pose.PORTRAIT,
        "scene": Scene.STUDIO,
        "style": Style.NONE,
    }
    values.update(overrides)
    return GenerationConfig(**values)


def test_resolve_text_replacements() -> None:
    resolver = PromptResolver.setup(make_config())
    resolved = resolver.resolve_text("{subject}, {appearance}, {wardrobe}, {pose}, {scene}")
    assert "adult person" in resolved
    assert "casual jacket" in resolved
    assert "standing portrait" in resolved
    assert "clean studio background" in resolved


def test_empty_style_is_removed() -> None:
    resolver = PromptResolver.setup(make_config())
    assert resolver.resolve_text("({style}:1.2), {subject}") == "adult person"


def test_non_empty_style_is_resolved() -> None:
    resolver = PromptResolver.setup(make_config(style=Style.WATERCOLOR))
    assert resolver.resolve_text("({style}:1.2), {subject}") == (
        "(watercolor painting, textured paper:1.2), adult person"
    )


@patch("src.workflows.Path.open")
@patch("src.workflows.Path.mkdir")
def test_resolve_file(mock_mkdir: MagicMock, mock_open: MagicMock) -> None:
    source = MagicMock()
    source.__enter__.return_value.read.return_value = "Hello {subject}!"
    destination = MagicMock()
    mock_open.side_effect = [source, destination]

    PromptResolver.setup(make_config()).resolve_file(Path("source.txt"), Path("destination.txt"))

    destination.__enter__.return_value.write.assert_called_once_with("Hello adult person!")


@patch("src.core.load_db")
def test_get_db_lookup_generates_unique_names(mock_load_db: MagicMock) -> None:
    from src.core import get_db_lookup

    get_db_lookup.cache_clear()
    mock_load_db.return_value = {
        "segments": [
            {"index": 0, "text": "NONE"},
            {"index": 1, "text": "soft light, warm palette"},
            {"index": 2, "text": "soft light, cool palette"},
        ]
    }

    lookup = get_db_lookup("styles", "default")

    assert lookup["NONE"] == ""
    assert lookup["SOFT LIGHT"] == "soft light, warm palette"
    assert lookup["SOFT LIGHT 2"] == "soft light, cool palette"
