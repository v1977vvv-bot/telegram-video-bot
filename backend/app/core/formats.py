from __future__ import annotations

from dataclasses import dataclass


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


def is_available_format(width: int, height: int) -> bool:
    return any(
        item.width == width and item.height == height for item in AVAILABLE_GENERATION_FORMATS
    )
