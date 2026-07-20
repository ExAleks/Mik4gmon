"""Оценка качества сигнала по RSRP, SINR, RSRQ."""

from __future__ import annotations

from typing import Any


_SIGNAL_LEVELS: dict[str, list[tuple[float, str, str]]] = {
    'rsrp': [
        (-80, 'Отличный', '#00c853'),
        (-90, 'Хороший', '#76ff03'),
        (-100, 'Средний', '#ffd600'),
        (-110, 'Плохой', '#ff6d00'),
        (-1200, 'Критический', '#d50000'),
    ],
    'rssi': [
        (-80, 'Отличный', '#00c853'),
        (-90, 'Хороший', '#76ff03'),
        (-100, 'Средний', '#ffd600'),
        (-110, 'Плохой', '#ff6d00'),
        (-1200, 'Критический', '#d50000'),
    ],
    'sinr': [
        (20, 'Отличный', '#00c853'),
        (13, 'Хороший', '#76ff03'),
        (5, 'Средний', '#ffd600'),
        (0, 'Плохой', '#ff6d00'),
        (-200, 'Критический', '#d50000'),
    ],
    'rsrq': [
        (-5, 'Отличный', '#00c853'),
        (-10, 'Хороший', '#76ff03'),
        (-15, 'Средний', '#ffd600'),
        (-20, 'Плохой', '#ff6d00'),
        (-400, 'Критический', '#d50000'),
    ],
}


def evaluate_signal(param: str, value: float) -> tuple[str, str, float]:
    """Возвращает (текст_статуса, цвет, нормированный_балл)."""
    levels = _SIGNAL_LEVELS.get(param, _SIGNAL_LEVELS['rsrp'])
    for threshold, label, color in levels:
        if value >= threshold:
            return label, color, 1.0
    return 'Критический', '#d50000', 0.0


def interpolate_color(start_color: str, end_color: str, t: float) -> str:
    """Линейная интерполяция между двумя hex-цветами."""
    if t <= 0:
        return start_color
    if t >= 1:
        return end_color
    sr = int(start_color[1:3], 16)
    sg = int(start_color[3:5], 16)
    sb = int(start_color[5:7], 16)
    er = int(end_color[1:3], 16)
    eg = int(end_color[3:5], 16)
    eb = int(end_color[5:7], 16)
    r = int(sr + (er - sr) * t)
    g = int(sg + (eg - sg) * t)
    b = int(sb + (eb - sb) * t)
    return f'#{r:02x}{g:02x}{b:02x}'


def signal_to_score(value: float, param: str) -> float:
    """Конвертирует значение сигнала в балл 0..1."""
    levels = _SIGNAL_LEVELS.get(param, _SIGNAL_LEVELS['rsrp'])
    for i, (threshold, label, color) in enumerate(levels):
        if value >= threshold:
            return 1.0 - i * 0.25
    return 0.0
