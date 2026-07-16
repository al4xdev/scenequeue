from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Subject(Enum):
    PERSON = "adult person"
    ROBOT = "friendly service robot"
    FOX = "red fox"


class Appearance(Enum):
    NONE = ""
    CASUAL = "natural appearance, relaxed expression"
    ADVENTURER = "weathered traveler, practical details"
    FUTURISTIC = "sleek futuristic design, subtle luminous accents"


class Wardrobe(Enum):
    NONE = ""
    CASUAL = "casual jacket, plain shirt, trousers"
    OUTDOOR = "weatherproof coat, hiking boots, small backpack"
    FORMAL = "tailored formal outfit"


class Pose(Enum):
    NONE = ""
    PORTRAIT = "standing portrait, looking toward camera"
    WALKING = "walking naturally, candid motion"
    SEATED = "seated comfortably, relaxed posture"


class Scene(Enum):
    NONE = ""
    STUDIO = "clean studio background, soft key light"
    FOREST = "quiet forest trail, morning mist"
    CITY = "modern city street, blue hour"


class Style(Enum):
    NONE = ""
    PHOTOGRAPHIC = "cinematic photography, realistic materials"
    ILLUSTRATION = "editorial illustration, clean shapes"
    WATERCOLOR = "watercolor painting, textured paper"


@dataclass(frozen=True)
class GenerationConfig:
    subject: str
    appearance: str
    wardrobe: str
    pose: str
    scene: str
    style: str = ""
