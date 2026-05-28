from __future__ import annotations

from dataclasses import dataclass

from shared.app.enums import VideoQuality


@dataclass(frozen=True, slots=True)
class GenerationFormat:
    label: str
    width: int
    height: int


AVAILABLE_GENERATION_FORMATS = (
    GenerationFormat(label="Горизонтальное 854×480", width=854, height=480),
    GenerationFormat(label="Квадратное 480×480", width=480, height=480),
    GenerationFormat(label="Вертикальное 480×854", width=480, height=854),
)

AVAILABLE_GENERATION_FORMATS_720P = (
    GenerationFormat(label="Горизонтальное 1280×720", width=1280, height=720),
    GenerationFormat(label="Квадратное 720×720", width=720, height=720),
    GenerationFormat(label="Вертикальное 720×1280", width=720, height=1280),
)


def available_formats_for_quality(quality_profile: str | None) -> tuple[GenerationFormat, ...]:
    if (quality_profile or VideoQuality.P480.value).strip().lower() == VideoQuality.P720.value:
        return AVAILABLE_GENERATION_FORMATS_720P
    return AVAILABLE_GENERATION_FORMATS


def default_format_for_quality(quality_profile: str | None) -> GenerationFormat:
    formats = available_formats_for_quality(quality_profile)
    return formats[1]


def is_available_format(
    width: int,
    height: int,
    quality_profile: str | None = VideoQuality.P480.value,
) -> bool:
    return any(
        item.width == width and item.height == height
        for item in available_formats_for_quality(quality_profile)
    )
