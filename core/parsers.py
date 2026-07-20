"""Парсинг AT-ответов модема."""

from __future__ import annotations

import re
from typing import Any

from core.constants import BAND_FREQ_MAP


def _clean_at_output(raw: str) -> str:
    """Убирает Echo-строку и лишние пробелы."""
    if not raw:
        return ''
    lines = raw.strip().split('\n')
    result = []
    for line in lines:
        line = line.strip()
        if line.upper().startswith('AT+') or line == 'OK':
            continue
        if line:
            result.append(line)
    return '\n'.join(result)


def parse_at_csq(raw: str) -> dict[str, Any]:
    """Парсит AT+CSQ."""
    data: dict[str, Any] = {}
    raw = _clean_at_output(raw)
    m = re.search(r'CSQ:\s*(\d+),', raw)
    if m:
        try:
            rssi_dbm = -113 + 2 * int(m.group(1))
            if rssi_dbm < -113:
                rssi_dbm = -113
            data['rssi'] = rssi_dbm
        except ValueError:
            pass
    return data


def parse_at_signal(raw: str) -> dict[str, Any]:
    """Парсит at+csq или at@errc:pcell_scell_measurement_info()."""
    data: dict[str, Any] = {}
    raw = _clean_at_output(raw)
    if not raw:
        return data
    m = re.search(r'rsrp\s*\[\s*([-\d.]+)', raw, re.IGNORECASE)
    if m:
        try:
            data['rsrp'] = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r'rsrq\s*\[\s*([-\d.]+)', raw, re.IGNORECASE)
    if m:
        try:
            data['rsrq'] = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r'rssnr\s*\[\s*([-\d.]+)', raw, re.IGNORECASE)
    if m:
        try:
            data['sinr'] = float(m.group(1))
        except ValueError:
            pass
    return data


def parse_at_cops(raw: str) -> str:
    """Парсит AT+COPS?."""
    raw = _clean_at_output(raw)
    m = re.search(r'COPS:\s*\d+,\d+,"([^"]*)"', raw)
    if m:
        return m.group(1)
    m = re.search(r'COPS:\s*\d+,\d+,([^,]+)', raw)
    if m:
        return m.group(1).strip('"')
    return '-'


def parse_at_cell_info(raw: str) -> dict[str, str]:
    """Парсит at@errc:cell_info()."""
    raw = _clean_at_output(raw)
    data: dict[str, str] = {}
    m = re.search(r'pci:\s*(\d+)', raw, re.IGNORECASE)
    if m:
        data['pci'] = m.group(1)
    m = re.search(r'earfcn_dl:\s*(\d+)', raw, re.IGNORECASE)
    if m:
        data['earfcn_dl'] = m.group(1)
    m = re.search(r'bandwidth_dl:\s*(\d+)', raw, re.IGNORECASE)
    if m:
        data['bandwidth_dl'] = m.group(1)
    return data


def format_bytes_mb(value: int) -> str:
    """Форматирует байты в МБ."""
    mb = value / 1048576
    if mb < 1:
        return f"{mb:.1f} МБ"
    return f"{mb:.0f} МБ"


def format_rate_mbps(value: float) -> str:
    """Форматирует скорость в Mbps."""
    return f"{value:.3f} Мбит/с"


def format_si_prefix(value: float, unit: str = '') -> str:
    """Красивый вывод с SI-приставками."""
    prefixes = [(1e6, 'М'), (1e3, 'к'), (1, '')]
    for divisor, prefix in prefixes:
        if abs(value) >= divisor:
            if divisor == 1:
                return f"{value:.0f} {prefix}{unit}"
            return f"{value / divisor:.1f} {prefix}{unit}"
    return f"{value:.1f}{unit}"
