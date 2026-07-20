# -*- coding: utf-8 -*-
"""Mik4gmon — монитор 4G LTE сигнала для Fibocom L850-GL через MikroTik RouterOS."""

from __future__ import annotations

import argparse
import atexit
import contextlib
import csv
import io
import json
import logging
import os
import platform
import queue
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, ttk
import traceback
import urllib.request
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import tkinter.messagebox as mb

pystray = None
Image = None
ImageDraw = None

with contextlib.suppress(ImportError):
    import pystray
    from PIL import Image, ImageDraw

from core import (
    ALL_LTE_BANDS,
    BAND_FREQ_MAP,
    EARFCN_RANGES,
    PROHIBITED_BANDS_RU,
    SIGNAL_THRESHOLDS,
    available_languages,
    evaluate_signal,
    format_bytes_mb,
    format_rate_mbps,
    parse_at_cell_info,
    parse_at_cops,
    parse_at_csq,
    parse_at_signal,
    set_language,
    t,
)

logger = logging.getLogger("mik4gmon")

VERSION = "0.0.1"
APP_NAME = "Mik4gmon"
GITHUB_REPO = "ExAleks/Mik4gmon"

API_PORT_DEFAULT = 8728
DEFAULT_PASSWORD = "1"
MONITOR_INTERVAL = 2.0
HISTORY_MAX = 50

_AT_LOCK = threading.Lock()

_FIBOCOM_BAND_MAP: dict[int, int] = {
    1: 1, 2: 2, 3: 4, 4: 8, 5: 16, 7: 64, 8: 128,
    12: 256, 13: 512, 17: 1024, 18: 2048, 19: 4096,
    20: 8192, 25: 65536, 26: 131072, 28: 262144,
    32: 1, 38: 1, 39: 2, 40: 4, 41: 8, 42: 16,
    43: 32, 66: 268435456, 71: 562949953421312,
}

_STANDARD_TO_FIBOCOM: dict[str, int] = {
    "B1": 1, "B2": 2, "B3": 4, "B4": 8, "B5": 16,
    "B7": 64, "B8": 128, "B12": 256, "B13": 512,
    "B17": 1024, "B18": 2048, "B19": 4096, "B20": 8192,
    "B25": 65536, "B26": 131072, "B28": 262144,
    "B32": 1, "B38": 1, "B39": 2, "B40": 4, "B41": 8,
    "B42": 16, "B43": 32, "B66": 268435456, "B71": 562949953421312,
}

PROHIBITED_BANDS_RU = ["B13", "B17"]


@dataclass
class RouterOSClient:
    host: str
    port: int
    password: str
    username: str = "admin"
    _sock: socket.socket | None = None
    _buffer: io.BytesIO = field(default_factory=io.BytesIO)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _connected: bool = False
    _tag_counter: int = 0

    ROS_API_LENGTH = {
        0x00: 0, 0x01: 1, 0x02: 2, 0x03: 3, 0x04: 4,
    }

    def _encode_length(self, length: int) -> bytes:
        if length < 0x80:
            return bytes([length])
        if length < 0x4000:
            return bytes([0x80 | (length >> 8), length & 0xFF])
        if length < 0x200000:
            return bytes([0xC0 | (length >> 16), (length >> 8) & 0xFF, length & 0xFF])
        if length < 0x10000000:
            return bytes([0xE0 | (length >> 24), (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
        return b'\xF0' + struct.pack('>I', length)

    def _read_length(self) -> int:
        buf = self._sock.recv(1) if self._sock else b''
        if not buf:
            raise ConnectionError("Socket closed")
        b = buf[0]
        if b & 0x80 == 0:
            return b
        if b & 0xC0 == 0x80:
            extra = self._sock.recv(1) if self._sock else b''
            if len(extra) < 1:
                raise ConnectionError("Socket closed")
            return ((b & 0x3F) << 8) | extra[0]
        if b & 0xE0 == 0xC0:
            extra = self._sock.recv(2) if self._sock else b''
            if len(extra) < 2:
                raise ConnectionError("Socket closed")
            return ((b & 0x1F) << 16) | (extra[0] << 8) | extra[1]
        if b & 0xF0 == 0xE0:
            extra = self._sock.recv(3) if self._sock else b''
            if len(extra) < 3:
                raise ConnectionError("Socket closed")
            return ((b & 0x0F) << 24) | (extra[0] << 16) | (extra[1] << 8) | extra[2]
        extra = self._sock.recv(4) if self._sock else b''
        if len(extra) < 4:
            raise ConnectionError("Socket closed")
        return struct.unpack('>I', extra)[0]

    def _send_word(self, word: bytes) -> None:
        if not self._sock:
            raise ConnectionError("Not connected")
        self._sock.sendall(self._encode_length(len(word)) + word)

    def _send_sentence(self, *words: str) -> None:
        for w in words:
            self._send_word(w.encode())
        self._send_word(b'')

    def _read_sentence(self) -> list[str]:
        result: list[str] = []
        while True:
            length = self._read_length()
            if length == 0:
                break
            data = b''
            while len(data) < length:
                chunk = self._sock.recv(length - len(data)) if self._sock else b''
                if not chunk:
                    raise ConnectionError("Socket closed during read")
                data += chunk
            result.append(data.decode('utf-8', errors='replace'))
        return result

    def connect(self) -> None:
        with self._lock:
            if self._connected:
                return
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10)
            try:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 30)
                if hasattr(socket, 'TCP_KEEPCNT'):
                    self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except (OSError, AttributeError):
                pass
            self._sock.connect((self.host, self.port))
            self._buffer = io.BytesIO()
            self._login()
            self._connected = True

    def _login(self) -> None:
        sentence = self._read_sentence()
        if not sentence:
            raise ConnectionError("No login prompt")
        while sentence[0] not in ('!done',):
            self._send_sentence('/login', f'=name={self.username}', f'=password={self.password}')
            resp = self._read_sentence()
            while resp and resp[0] not in ('!done', '!trap'):
                resp = self._read_sentence()
            if resp and resp[0] == '!trap':
                raise ConnectionError(f"Login failed: {resp}")
            sentence = resp

    def cmd(self, command: str, *params: str) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {}
            self._tag_counter += 1
            tag = str(self._tag_counter)
            try:
                self._send_sentence(command, f'.tag={tag}', *params)
                while True:
                    sentence = self._read_sentence()
                    if not sentence:
                        raise ConnectionError("Empty reply")
                    reply_type = sentence[0]
                    if reply_type == '!done':
                        break
                    if reply_type == '!trap':
                        result['error'] = sentence[1] if len(sentence) > 1 else 'Unknown error'
                        break
                    if reply_type == '!re':
                        for item in sentence[1:]:
                            if item.startswith('='):
                                key, _, val = item[1:].partition('=')
                                result[key] = val
                    elif reply_type == '!recv':
                        result['recv'] = sentence[1] if len(sentence) > 1 else ''
            except (socket.timeout, OSError) as e:
                self._connected = False
                raise ConnectionError(str(e))
            return result

    def raw_cmd(self, command: str, *params: str) -> list[dict[str, str]]:
        with self._lock:
            results: list[dict[str, str]] = []
            self._tag_counter += 1
            tag = str(self._tag_counter)
            try:
                self._send_sentence(command, f'.tag={tag}', *params)
                while True:
                    sentence = self._read_sentence()
                    if not sentence:
                        break
                    reply_type = sentence[0]
                    if reply_type == '!done':
                        break
                    if reply_type == '!trap':
                        err = sentence[1] if len(sentence) > 1 else 'Unknown error'
                        results.append({'error': err})
                        break
                    if reply_type == '!re':
                        row: dict[str, str] = {}
                        for item in sentence[1:]:
                            if item.startswith('='):
                                key, _, val = item[1:].partition('=')
                                row[key] = val
                        results.append(row)
            except (socket.timeout, OSError) as e:
                self._connected = False
                raise ConnectionError(str(e))
            return results

    def disconnect(self) -> None:
        with self._lock:
            if self._sock:
                with contextlib.suppress(OSError):
                    self._send_sentence('/quit')
            if self._sock:
                self._sock.close()
                self._sock = None
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._sock is not None


class StatCollector:
    def __init__(self, maxlen: int = 100) -> None:
        self._rx = deque(maxlen=maxlen)
        self._tx = deque(maxlen=maxlen)
        self._timestamps = deque(maxlen=maxlen)
        self._peak_rx = 0.0
        self._peak_tx = 0.0

    def add(self, rx_mbps: float, tx_mbps: float) -> None:
        now = time.time()
        self._rx.append(rx_mbps)
        self._tx.append(tx_mbps)
        self._timestamps.append(now)
        self._peak_rx = max(self._peak_rx, rx_mbps)
        self._peak_tx = max(self._peak_tx, tx_mbps)

    @property
    def peak_rx(self) -> float:
        return self._peak_rx

    @property
    def peak_tx(self) -> float:
        return self._peak_tx

    def reset_peaks(self) -> None:
        self._peak_rx = 0.0
        self._peak_tx = 0.0

    def last_n(self, n: int) -> tuple[list[float], list[float], list[float]]:
        cnt = min(n, len(self._rx))
        if cnt == 0:
            return [], [], []
        return (
            list(self._rx)[-cnt:],
            list(self._tx)[-cnt:],
            [ts - self._timestamps[0] for ts in (list(self._timestamps)[-cnt:])],
        )


class TowerHistory:
    def __init__(self, maxlen: int = HISTORY_MAX) -> None:
        self._entries: list[dict[str, str]] = []
        self._maxlen = maxlen

    def add(self, entry: dict[str, str]) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._maxlen:
            self._entries.pop(0)

    @property
    def entries(self) -> list[dict[str, str]]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()


class SpeedGraph(tk.Canvas):
    def __init__(self, parent: tk.Widget, width: int = 400, height: int = 150, **kwargs: Any) -> None:
        super().__init__(parent, width=width, height=height, highlightthickness=0, **kwargs)
        self._width = width
        self._height = height
        self._series: dict[str, dict[str, Any]] = {}
        self._max_rate = 1.0
        self._data_points = 60
        self._point_w = 0.0
        self._bg_color = "#1e1e2e"
        self._grid_color = "#313244"
        self._text_color = "#cdd6f4"
        self._init_ui()

    def _init_ui(self) -> None:
        self.configure(bg=self._bg_color)
        self._point_w = self._width / self._data_points

    def set_bg_color(self, color: str) -> None:
        self._bg_color = color
        self.configure(bg=color)

    def set_text_color(self, color: str) -> None:
        self._text_color = color

    def set_grid_color(self, color: str) -> None:
        self._grid_color = color

    def add_series(self, name: str, color: str, data: list[float]) -> None:
        self._series[name] = {'color': color, 'data': data}

    def update_data(self, name: str, data: list[float]) -> None:
        if name in self._series:
            self._series[name]['data'] = data
        else:
            self._series[name] = {'color': '#0078D7', 'data': data}

    def refresh(self) -> None:
        self.delete('all')
        w = self._width
        h = self._height
        self.create_rectangle(0, 0, w, h, fill=self._bg_color, outline='')

        max_val = 1.0
        for sdata in self._series.values():
            if sdata['data']:
                m = max(sdata['data'])
                if m > max_val:
                    max_val = m
        max_val = max(max_val, 0.1)
        self._max_rate = max_val

        for i in range(0, 5):
            y = h - 1 - (h - 20) * (i / 4) - 10
            val = max_val * (i / 4)
            self.create_line(50, y, w - 5, y, fill=self._grid_color, width=1)
            self.create_text(46, y, text=f'{val:.0f}', anchor='e', fill=self._text_color, font=('Segoe UI', 7))

        for sname, sdata in self._series.items():
            pts = sdata['data']
            if len(pts) < 2:
                continue
            color = sdata['color']
            coords: list[float] = []
            for i, v in enumerate(pts):
                x = 50 + (w - 55) * (i / (len(pts) - 1))
                y = h - 10 - (h - 20) * (v / max_val)
                coords.extend([x, y])
            if len(coords) >= 4:
                self.create_line(*coords, fill=color, width=2, smooth=True)


class CellScanWindow(tk.Toplevel):
    def __init__(self, parent: tk.Widget, api: RouterOSClient, iface_id: str, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self.title(t("Скан окружения"))
        self.geometry("700x400")
        self.api = api
        self.iface_id = iface_id
        self._scanning = False
        self._init_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill='x', padx=5, pady=5)
        self._scan_btn = ttk.Button(top, text=t("🔍 Сканировать"), command=self._start_scan)
        self._scan_btn.pack(side='left')
        self._status_lbl = ttk.Label(top, text="")
        self._status_lbl.pack(side='left', padx=10)

        columns = ("earfcn", "pci", "rsrp", "band", "type")
        self._tree = ttk.Treeview(self, columns=columns, show='headings', height=12)
        for col, w, txt in [("earfcn", 80, "EARFCN"), ("pci", 60, "PCI"),
                             ("rsrp", 80, "RSRP"), ("band", 80, "Band"), ("type", 100, "Type")]:
            self._tree.heading(col, text=txt)
            self._tree.column(col, width=w, anchor='center')
        self._tree.pack(fill='both', expand=True, padx=5, pady=5)

        scroll = ttk.Scrollbar(self, orient='vertical', command=self._tree.yview)
        scroll.pack(side='right', fill='y')
        self._tree.configure(yscrollcommand=scroll.set)

    def _start_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.configure(state='disabled')
        self._status_lbl.configure(text=t("Сканирование..."))
        for item in self._tree.get_children():
            self._tree.delete(item)
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self) -> None:
        try:
            cells = self._scan_cells()
            self.after(0, self._update_results, cells)
        except Exception as e:
            self.after(0, self._show_error, str(e))

    def _scan_cells(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        if not self.api or not self.api.is_connected:
            return results
        with _AT_LOCK:
            raw = self._do_at_chat("at@errc:scan_result():10")
        lines = raw.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line or line.startswith('@') or 'scan_result' in line or '"ERROR"' in line:
                continue
            m = re.search(r'(earfcn|pci|rsrp)', line, re.IGNORECASE)
            if not m:
                continue
            parts = re.split(r'[,;()]+', line)
            entry: dict[str, str] = {}
            for part in parts:
                part = part.strip()
                kv = part.split(':', 1)
                if len(kv) == 2:
                    k = kv[0].strip().lower()
                    v = kv[1].strip().strip('"').strip("'")
                    if k in ('earfcn', 'pci', 'rsrp', 'band', 'type'):
                        entry[k] = v
            if entry and 'earfcn' in entry:
                results.append(entry)
        return results

    def _do_at_chat(self, cmd: str) -> str:
        try:
            r = self.api.cmd('/interface/lte/at-chat', f'=.id={self.iface_id}',
                            f'=input={cmd}', '=wait=yes')
            return r.get('recv', '')
        except Exception:
            return ''

    def _update_results(self, cells: list[dict[str, str]]) -> None:
        self._scan_btn.configure(state='normal')
        self._scanning = False
        if not cells:
            self._status_lbl.configure(text=t("Ничего не найдено"))
            return
        self._status_lbl.configure(text=t("Найдено: {n}").format(n=len(cells)))
        for cell in cells:
            self._tree.insert('', 'end', values=(
                cell.get('earfcn', '-'),
                cell.get('pci', '-'),
                cell.get('rsrp', '-'),
                cell.get('band', '-'),
                cell.get('type', '-'),
            ))

    def _show_error(self, err: str) -> None:
        self._scan_btn.configure(state='normal')
        self._scanning = False
        self._status_lbl.configure(text=f"{t('Ошибка')}: {err}")

    def _on_close(self) -> None:
        self.destroy()


class AlertsWindow(tk.Toplevel):
    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self.title(t("⚠ Алерт при слабом сигнале"))
        self.geometry("400x300")
        self.result: dict[str, Any] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        ttk.Label(self, text=t("RSRP порог (dBm):")).pack(anchor='w', padx=10, pady=(10, 0))
        self._threshold_var = tk.StringVar(value="-110")
        ttk.Entry(self, textvariable=self._threshold_var, width=10).pack(anchor='w', padx=10)

        self._sound_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Звуковое оповещение", variable=self._sound_var).pack(anchor='w', padx=10, pady=5)

        self._popup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Всплывающее окно", variable=self._popup_var).pack(anchor='w', padx=10)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=20)
        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side='left', padx=5)
        ttk.Button(btn_frame, text=t("Отключено"), command=self._on_cancel).pack(side='left', padx=5)

    def _on_ok(self) -> None:
        try:
            self.result['threshold'] = int(self._threshold_var.get())
        except ValueError:
            mb.showerror("Ошибка", "Неверный порог")
            return
        self.result['sound'] = self._sound_var.get()
        self.result['popup'] = self._popup_var.get()
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = {}
        self.destroy()


class BandManagementWindow(tk.Toplevel):
    def __init__(self, parent: tk.Widget, api: RouterOSClient, iface_id: str, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self.title(t("Управление бендами LTE"))
        self.geometry("600x500")
        self.api = api
        self.iface_id = iface_id
        self._vars: dict[str, tk.BooleanVar] = {}
        self._init_ui()
        self._load_current_bands()

    def _init_ui(self) -> None:
        main = ttk.Frame(self)
        main.pack(fill='both', expand=True, padx=10, pady=10)
        ttk.Label(main, text=t("Выберите бенды для сканирования/выбора"), font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self._frame = ttk.Frame(main)
        self._frame.pack(fill='both', expand=True, pady=10)
        self._info_lbl = ttk.Label(main, text="")
        self._info_lbl.pack(anchor='w')
        btn_frame = ttk.Frame(main)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text=t("Применить"), command=self._apply).pack(side='left', padx=5)

    def _load_current_bands(self) -> None:
        threading.Thread(target=self._do_load, daemon=True).start()

    def _do_load(self) -> None:
        try:
            current = self._get_active_bands()
            self.after(0, self._populate_ui, current)
        except Exception as e:
            self.after(0, lambda: self._info_lbl.configure(text=f"Error: {e}"))

    def _get_active_bands(self) -> int:
        with _AT_LOCK:
            raw = self._do_at("at+xact?")
        m = re.search(r'XACT:\s*(\d+)', raw)
        if m:
            return int(m.group(1))
        return 0

    def _do_at(self, cmd: str) -> str:
        try:
            r = self.api.cmd('/interface/lte/at-chat', f'=.id={self.iface_id}',
                            f'=input={cmd}', '=wait=yes')
            return r.get('recv', '')
        except Exception:
            return ''

    def _populate_ui(self, current_mask: int) -> None:
        for child in self._frame.winfo_children():
            child.destroy()
        self._vars.clear()
        for band_num in ALL_LTE_BANDS:
            band_key = f"B{band_num}"
            mask = _STANDARD_TO_FIBOCOM.get(band_key, 0)
            if mask == 0:
                continue
            var = tk.BooleanVar(value=bool(current_mask & mask))
            self._vars[band_key] = var
            freq = BAND_FREQ_MAP.get(band_num, '?')
            cb = ttk.Checkbutton(self._frame, text=f"{band_key} ({freq} MHz)", variable=var)
            cb.pack(anchor='w')

    def _apply(self) -> None:
        new_mask = 0
        for band_key, var in self._vars.items():
            if var.get():
                new_mask |= _STANDARD_TO_FIBOCOM.get(band_key, 0)
        if new_mask == 0:
            mb.showwarning("Предупреждение", "Не выбран ни один бенд")
            return
        prohibited = [b for b in PROHIBITED_BANDS_RU if self._vars.get(b, tk.BooleanVar()).get()]
        if prohibited:
            msg = f"Выбраны запрещённые бенды: {', '.join(prohibited)}\nПродолжить?"
            if not mb.askyesno("Предупреждение", msg):
                return
        self._info_lbl.configure(text=t("Отправлено: {codes}").format(codes=str(new_mask)))
        threading.Thread(target=self._do_apply, args=(new_mask,), daemon=True).start()

    def _do_apply(self, mask: int) -> None:
        with _AT_LOCK:
            raw = self._do_at(f"at+xact={mask}")
        self.after(0, lambda: self._info_lbl.configure(text=f"Applied: mask={mask}"))


class Application:
    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.title(f"{APP_NAME} v{VERSION}")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._setup_style()
        self._load_icon()
        self._dark_mode = False
        self._apply_theme()
        self._center_window(1200, 780)

        self._api = RouterOSClient(
            host="192.168.88.1",
            port=API_PORT_DEFAULT,
            password=DEFAULT_PASSWORD,
        )
        self._monitor_running = False
        self._monitor_thread: threading.Thread | None = None
        self._monitor_interval = MONITOR_INTERVAL
        self._tick_counter = 0
        self._tray_icon: Any = None
        self._after_id: str | None = None
        self._always_on_top = False
        self._alert_enabled = False
        self._alert_threshold = -110
        self._alert_sound = True
        self._alert_popup = True
        self._last_alert_time = 0.0

        self._stat = StatCollector(maxlen=300)
        self._tower_history = TowerHistory()
        self._rx_prev = 0
        self._tx_prev = 0
        self._ts_prev = 0.0
        self._bytes_rx_prev = 0
        self._bytes_tx_prev = 0

        self._lte_info: dict[str, str] = {}
        self._sys_info: dict[str, str] = {}
        self._signal_data: dict[str, Any] = {}
        self._prev_cell_id = ""
        self._cell_scan_window: CellScanWindow | None = None
        self._speed_graph = None
        self._init_ui()

    def _load_icon(self) -> None:
        icon_path = Path("icon.ico")
        if icon_path.exists():
            try:
                img = tk.PhotoImage(file=str(icon_path))
                self._root.iconphoto(True, img)
                self._icon_img = img
            except Exception:
                pass

    def _setup_style(self) -> None:
        self._style = ttk.Style()
        self._style.theme_use('clam')

    def _center_window(self, w: int, h: int) -> None:
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self._root.geometry(f"{w}x{h}+{x}+{y}")

    def _init_ui(self) -> None:
        self._create_menu()
        main = ttk.Frame(self._root)
        main.pack(fill='both', expand=True, padx=5, pady=5)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        self._create_connection_frame(main)
        self._create_signal_frame(main)
        self._create_tower_frame(main)
        self._create_speed_frame(main)
        self._create_statusbar()

    def _create_menu(self) -> None:
        menubar = tk.Menu(self._root)
        self._root.configure(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=t("📊 Состояние"), menu=file_menu)
        file_menu.add_command(label=t("Обновить данные"), command=self._force_refresh)
        file_menu.add_separator()
        file_menu.add_command(label=t("Перезагрузить роутер"), command=self._reboot_router)
        file_menu.add_separator()
        file_menu.add_command(label=t("Очистить пики"), command=self._clear_peaks)

        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=t("⚙️ Подключение"), menu=settings_menu)
        settings_menu.add_command(label=t("⚠ Алерт при слабом сигнале"), command=self._open_alerts)
        settings_menu.add_command(label=t("Управление бендами LTE"), command=self._open_band_mgmt)
        settings_menu.add_separator()
        settings_menu.add_command(label=t("Скан окружения"), command=self._open_cell_scan)
        settings_menu.add_separator()
        self._ontop_var = tk.BooleanVar(value=False)
        settings_menu.add_checkbutton(label=t("Поверх окон"), variable=self._ontop_var, command=self._toggle_ontop)
        self._dark_var = tk.BooleanVar(value=False)
        settings_menu.add_checkbutton(label=t("🌙"), variable=self._dark_var, command=self._toggle_dark)

        lang_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="🌐", menu=lang_menu)
        self._lang_var = tk.StringVar(value="ru")
        for code in available_languages():
            lang_menu.add_radiobutton(label=code.upper(), variable=self._lang_var, value=code, command=self._switch_lang)

    def _create_connection_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=t("⚙️ Подключение"), padding=5)
        frame.grid(row=0, column=0, sticky='nsew', padx=2, pady=2)
        fields = [
            (t("IP роутера:"), "host", "192.168.88.1"),
            (t("Пароль:"), "password", DEFAULT_PASSWORD),
            (t("LTE интерфейс:"), "iface", "LTE1"),
            (t("Порт API:"), "port", str(API_PORT_DEFAULT)),
            (t("Интервал обновления (с):"), "interval", str(MONITOR_INTERVAL)),
        ]
        self._entry_vars: dict[str, tk.StringVar] = {}
        for i, (label, key, default) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky='w', pady=1)
            var = tk.StringVar(value=default)
            self._entry_vars[key] = var
            kw = {"textvariable": var, "width": 18}
            if key == "password":
                kw["show"] = "*"
            ttk.Entry(frame, **kw).grid(row=i, column=1, sticky='ew', pady=1, padx=5)
        frame.columnconfigure(1, weight=1)
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=5)
        self._connect_btn = ttk.Button(btn_frame, text=t("🚀 Подключиться"), command=self._toggle_connect)
        self._connect_btn.pack(side='left', padx=2)
        self._conn_status = ttk.Label(btn_frame, text=t("Отключено"), foreground="gray")
        self._conn_status.pack(side='left', padx=5)

    def _create_signal_frame(self, parent: ttk.Frame) -> None:
        self._signal_frame = ttk.LabelFrame(parent, text=t("📈 Монитор"), padding=5)
        self._signal_frame.grid(row=0, column=1, sticky='nsew', padx=2, pady=2)
        labels = [
            ("rsrp", "RSRP", "-"),
            ("sinr", "SINR", "-"),
            ("rsrq", "RSRQ", "-"),
            ("rssi", "RSSI", "-"),
            ("band", t("Рабочий Band (LTE)"), "-"),
            ("operator", t("Оператор (PLMN)"), "-"),
            ("cell_id", "Cell ID", "-"),
            ("rat", t("Тип сети (RAT)"), "-"),
            ("aggr", t("Агрегация (CA)"), "-"),
        ]
        self._signal_labels: dict[str, ttk.Label] = {}
        for i, (key, display, _) in enumerate(labels):
            ttk.Label(self._signal_frame, text=f"{display}:").grid(row=i, column=0, sticky='w', pady=1)
            lbl = ttk.Label(self._signal_frame, text="-", font=('Segoe UI', 9, 'bold'))
            lbl.grid(row=i, column=1, sticky='w', padx=5)
            self._signal_labels[key] = lbl

    def _create_tower_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=t("🗼 Вышка"), padding=5)
        frame.grid(row=1, column=0, sticky='nsew', padx=2, pady=2)
        columns = ("time", "cell_id", "rsrp", "sinr", "band", "operator")
        self._tower_tree = ttk.Treeview(frame, columns=columns, show='headings', height=6)
        headings = [
            (t("Время"), 120), ("Cell ID", 90), ("RSRP", 70),
            ("SINR", 70), ("Band", 60), (t("Оператор (PLMN)"), 100),
        ]
        for col, (text, w) in zip(columns, headings):
            self._tower_tree.heading(col, text=text)
            self._tower_tree.column(col, width=w, anchor='center')
        self._tower_tree.pack(fill='both', expand=True)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self._tower_tree.yview)
        scroll.pack(side='right', fill='y')
        self._tower_tree.configure(yscrollcommand=scroll.set)

    def _create_speed_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=t("График скорости"), padding=5)
        frame.grid(row=1, column=1, sticky='nsew', padx=2, pady=2)
        self._speed_graph = SpeedGraph(frame, width=400, height=150)
        self._speed_graph.pack(fill='both', expand=True)
        info_frame = ttk.Frame(frame)
        info_frame.pack(fill='x', pady=2)
        self._peak_rx_lbl = ttk.Label(info_frame, text=f"{t('Download')} ↓: -")
        self._peak_rx_lbl.pack(side='left', padx=5)
        self._peak_tx_lbl = ttk.Label(info_frame, text=f"{t('Upload')} ↑: -")
        self._peak_tx_lbl.pack(side='left', padx=5)
        ttk.Button(info_frame, text=t("Очистить пики"), command=self._clear_peaks).pack(side='right')

    def _create_statusbar(self) -> None:
        self._statusbar = ttk.Label(self._root, text=t("Нет данных"), relief='sunken', anchor='w')
        self._statusbar.pack(fill='x', side='bottom', padx=2, pady=2)

    def _apply_theme(self) -> None:
        if self._dark_mode:
            self._apply_dark_theme()
        else:
            self._apply_system_theme()

    def _apply_system_theme(self) -> None:
        try:
            import ctypes
            COLOR_WINDOW = 5
            COLOR_BTNFACE = 15
            COLOR_WINDOWTEXT = 8
            c = ctypes.windll.user32.GetSysColor
            bg = f'#{c(COLOR_WINDOW) & 0xFF:02x}{(c(COLOR_WINDOW) >> 8) & 0xFF:02x}{(c(COLOR_WINDOW) >> 16) & 0xFF:02x}'
            fg = f'#{c(COLOR_WINDOWTEXT) & 0xFF:02x}{(c(COLOR_WINDOWTEXT) >> 8) & 0xFF:02x}{(c(COLOR_WINDOWTEXT) >> 16) & 0xFF:02x}'
            btn = f'#{c(COLOR_BTNFACE) & 0xFF:02x}{(c(COLOR_BTNFACE) >> 8) & 0xFF:02x}{(c(COLOR_BTNFACE) >> 16) & 0xFF:02x}'
            self._root.configure(bg=bg)
            self._style.configure('TLabel', background=bg, foreground=fg)
            self._style.configure('TFrame', background=bg)
            self._style.configure('TLabelFrame', background=bg, foreground=fg)
            self._style.configure('TButton', background=btn, foreground=fg)
            self._style.configure('TLabelframe.Label', background=bg, foreground=fg)
            self._style.configure('Treeview', background=bg, foreground=fg, fieldbackground=bg)
            if self._speed_graph:
                self._speed_graph.set_bg_color(bg)
                self._speed_graph.set_text_color(fg)
                self._speed_graph.set_grid_color('#c0c0c0')
        except Exception:
            pass

    def _apply_dark_theme(self) -> None:
        bg = "#1e1e2e"
        fg = "#cdd6f4"
        sel = "#45475a"
        self._root.configure(bg=bg)
        self._style.configure('TLabel', background=bg, foreground=fg)
        self._style.configure('TFrame', background=bg)
        self._style.configure('TLabelFrame', background=bg, foreground=fg)
        self._style.configure('TButton', background=sel, foreground=fg)
        self._style.configure('TLabelframe.Label', background=bg, foreground=fg)
        self._style.configure('Treeview', background=bg, foreground=fg, fieldbackground=bg)
        self._style.map('Treeview', background=[('selected', sel)])
        if self._speed_graph:
            self._speed_graph.set_bg_color("#1e1e2e")
            self._speed_graph.set_text_color("#cdd6f4")
            self._speed_graph.set_grid_color("#313244")

    def _toggle_dark(self) -> None:
        self._dark_mode = self._dark_var.get()
        self._apply_theme()

    def _toggle_ontop(self) -> None:
        self._always_on_top = self._ontop_var.get()
        self._root.attributes('-topmost', self._always_on_top)

    def _switch_lang(self) -> None:
        set_language(self._lang_var.get())
        mb.showinfo("Info", "Restart required for language change")

    def _open_alerts(self) -> None:
        w = AlertsWindow(self._root)
        self._root.wait_window(w)
        if w.result:
            self._alert_enabled = True
            self._alert_threshold = w.result.get('threshold', -110)
            self._alert_sound = w.result.get('sound', True)
            self._alert_popup = w.result.get('popup', True)

    def _open_band_mgmt(self) -> None:
        iface = self._entry_vars['iface'].get()
        BandManagementWindow(self._root, self._api, iface)

    def _open_cell_scan(self) -> None:
        iface = self._entry_vars['iface'].get()
        self._cell_scan_window = CellScanWindow(self._root, self._api, iface)
        self._cell_scan_window.grab_set()

    def _toggle_connect(self) -> None:
        if self._api.is_connected:
            self._stop_monitor()
            self._api.disconnect()
            self._connect_btn.configure(text=t("🚀 Подключиться"))
            self._conn_status.configure(text=t("Отключено"), foreground="gray")
            self._destroy_tray()
        else:
            self._start_connect()

    def _start_connect(self) -> None:
        host = self._entry_vars['host'].get()
        port_str = self._entry_vars['port'].get()
        pwd = self._entry_vars['password'].get()
        interval_str = self._entry_vars['interval'].get()
        iface = self._entry_vars['iface'].get()
        try:
            port = int(port_str)
            interval = float(interval_str)
        except ValueError:
            mb.showerror("Error", "Invalid port/interval")
            return
        self._api.host = host
        self._api.port = port
        self._api.password = pwd
        self._monitor_interval = interval
        self._conn_status.configure(text=t("Подключение к роутеру..."), foreground="orange")
        self._connect_btn.configure(state='disabled')
        threading.Thread(target=self._do_connect, args=(iface,), daemon=True).start()

    def _do_connect(self, iface: str) -> None:
        try:
            self._api.connect()
            self._iface_id = self._resolve_interface(iface)
            self._root.after(0, self._on_connected)
        except Exception as e:
            self._root.after(0, lambda: self._on_connect_error(str(e)))

    def _resolve_interface(self, name: str) -> str:
        try:
            interfaces = self._api.raw_cmd('/interface/lte/print')
            for iface in interfaces:
                if iface.get('name', '').lower() == name.lower():
                    return iface.get('.id', name)
            return name
        except Exception:
            return name

    def _on_connected(self) -> None:
        self._connect_btn.configure(state='normal', text=t("⏹ Отключиться"))
        self._conn_status.configure(text=t("Подключено"), foreground="green")
        self._monitor_running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        self._create_tray()
        self._after_id = self._safe_after(int(self._monitor_interval * 1000), self._update_ui)
        self._check_updates()

    def _on_connect_error(self, err: str) -> None:
        self._connect_btn.configure(state='normal')
        self._conn_status.configure(text=t("Ошибка"), foreground="red")
        mb.showerror(t("Ошибка подключения"), t("Связь с роутером не удалась:\n\n{err}").format(err=err))

    def _safe_after(self, ms: int, cb: Callable[[], None]) -> str | None:
        try:
            return self._root.after(ms, cb)
        except (RuntimeError, tk.TclError):
            return None

    def _monitor_loop(self) -> None:
        while self._monitor_running:
            try:
                self._collect_data()
            except ConnectionError:
                self._monitor_running = False
                self._root.after(0, self._on_disconnect)
                break
            except Exception:
                logger.exception("Monitor loop error")
            time.sleep(self._monitor_interval)
            self._tick_counter += 1
            if self._tick_counter % 30 == 0:
                try:
                    self._api.cmd('/system/identity/print')
                except ConnectionError:
                    self._monitor_running = False
                    self._root.after(0, self._on_disconnect)
                    break

    def _collect_data(self) -> None:
        if not self._api.is_connected:
            raise ConnectionError("Not connected")
        self._collect_system_info()
        self._collect_lte_info()
        self._collect_signal()
        self._collect_traffic()

    def _collect_system_info(self) -> None:
        try:
            r = self._api.raw_cmd('/system/resource/print')
            if r:
                self._sys_info = r[0]
        except Exception:
            pass

    def _collect_lte_info(self) -> None:
        try:
            r = self._api.raw_cmd('/interface/lte/print')
            for iface in r:
                if iface.get('name', '').lower() == self._entry_vars['iface'].get().lower():
                    self._lte_info = iface
                    break
        except Exception:
            pass

    def _collect_signal(self) -> None:
        try:
            with _AT_LOCK:
                raw_csq = self._do_at("at+csq")
                raw_xlec = self._do_at("at+xlec?")
                raw_xact = self._do_at("at+xact?")
                raw_cops = self._do_at("at+cops?")
                raw_cereg = self._do_at("at+cereg?")
            data: dict[str, Any] = {}
            csq_data = parse_at_csq(raw_csq)
            if 'rssi' in csq_data:
                data['rssi'] = csq_data['rssi']
            data['raw_csq'] = raw_csq
            data['raw_xlec'] = raw_xlec
            data['raw_xact'] = raw_xact
            data['raw_cops'] = raw_cops
            data['raw_cereg'] = raw_cereg
            self._signal_data = data
        except Exception as e:
            logger.warning("Signal collect error: %s", e)

    def _do_at(self, cmd: str) -> str:
        try:
            iface = self._entry_vars['iface'].get()
            r = self._api.cmd('/interface/lte/at-chat', f'=.id={self._iface_id}',
                            f'=input={cmd}', '=wait=yes')
            return r.get('recv', '')
        except Exception:
            return ''

    def _collect_traffic(self) -> None:
        try:
            r = self._api.raw_cmd('/interface/monitor-traffic', f'=.id={self._iface_id}', '=once')
            if r:
                rx = int(r[0].get('rx-bits-per-second', 0))
                tx = int(r[0].get('tx-bits-per-second', 0))
                now = time.time()
                rx_mbps = rx / 1_000_000
                tx_mbps = tx / 1_000_000
                self._stat.add(rx_mbps, tx_mbps)
        except Exception:
            pass

    def _update_ui(self) -> None:
        if not self._monitor_running:
            return
        try:
            self._update_system_info()
            self._update_signal_labels()
            self._update_speed_graph()
            self._update_tower_history()
            self._check_alert()
        except Exception:
            pass
        self._after_id = self._safe_after(int(self._monitor_interval * 1000), self._update_ui)

    def _update_system_info(self) -> None:
        info = self._sys_info
        uptime = info.get('uptime', '-')
        cpu = info.get('cpu-load', '-')
        free_mem = info.get('free-memory', '0')
        total_mem = info.get('total-memory', '0')
        try:
            free_mb = int(free_mem) / 1048576
            total_mb = int(total_mem) / 1048576
        except ValueError:
            free_mb = 0
            total_mb = 0
        self._signal_labels.get('uptime', ttk.Label()).configure(text=f"{uptime}")
        self._statusbar.configure(
            text=f"CPU: {cpu}% | {t('Свободная память')}: {free_mb:.0f}/{total_mb:.0f} MB"
        )

    def _parse_at_aggregation(self, raw_xlec: str) -> str:
        if not raw_xlec:
            return "-"
        for line in raw_xlec.split('\n'):
            line = line.strip()
            if line.startswith('XLEC'):
                parts = line.split(':')
                if len(parts) >= 2:
                    fields = parts[1].strip().split(',')
                    if fields and fields[0].strip() == '0':
                        return "-"
        return "CA"

    def _parse_active_band(self, raw_xlec: str, raw_xact: str) -> str:
        band_str = "-"
        if raw_xlec:
            for line in raw_xlec.split('\n'):
                line = line.strip()
                if line.startswith('XLEC'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        fields = parts[1].strip().split(',')
                        if len(fields) >= 5:
                            band_num = fields[4].strip()
                            if band_num and band_num != '0':
                                band_str = f"B{band_num}"
                                break
        if band_str == "-" and raw_xact:
            m = re.search(r'XACT:\s*(\d+)', raw_xact)
            if m:
                mask = int(m.group(1))
                for bn in sorted(ALL_LTE_BANDS, reverse=True):
                    bmask = _STANDARD_TO_FIBOCOM.get(f"B{bn}", 0)
                    if bmask and mask & bmask:
                        band_str = f"B{bn}"
                        break
        return band_str

    def _update_signal_labels(self) -> None:
        data = self._signal_data
        if not data:
            return
        rsrp = self._extract_param("rsrp")
        sinr = self._extract_param("sinr")
        rsrq = self._extract_param("rsrq")
        rssi_val = data.get('rssi', '-')
        raw_cops = data.get('raw_cops', '')
        raw_xlec = data.get('raw_xlec', '')
        raw_xact = data.get('raw_xact', '')
        raw_cereg = data.get('raw_cereg', '')

        operator = parse_at_cops(raw_cops)
        self._signal_labels['operator'].configure(text=operator if operator else '-')
        self._signal_labels['rsrp'].configure(text=f"{rsrp} dBm" if rsrp != '-' else '-')
        self._signal_labels['sinr'].configure(text=f"{sinr} dB" if sinr != '-' else '-')
        self._signal_labels['rsrq'].configure(text=f"{rsrq} dB" if rsrq != '-' else '-')
        self._signal_labels['rssi'].configure(text=f"{rssi_val} dBm" if rssi_val != '-' else '-')

        band = self._parse_active_band(raw_xlec, raw_xact)
        self._signal_labels['band'].configure(text=band)

        aggr = self._parse_at_aggregation(raw_xlec)
        self._signal_labels['aggr'].configure(text=aggr)

        cell_id = '-'
        if raw_cereg:
            m = re.search(r'CEREG:\s*\d+,\d+,"?([^",]+)', raw_cereg)
            if m:
                cid = m.group(1)
                try:
                    cell_id = str(int(cid, 16))
                except ValueError:
                    cell_id = cid
        self._signal_labels['cell_id'].configure(text=cell_id)

        rat = '-'
        if raw_cereg:
            m = re.search(r'CEREG:\s*\d+,\d+,[^,]*,(\d+)', raw_cereg)
            if m:
                rat_code = m.group(1)
                rat_map = {'7': 'LTE', '9': 'LTE-A', '2': 'UTRAN', '4': 'HSDPA'}
                rat = rat_map.get(rat_code, f'RAT:{rat_code}')
        self._signal_labels['rat'].configure(text=rat)

    def _extract_param(self, param: str) -> Any:
        data = self._signal_data
        raw_xlec = data.get('raw_xlec', '')
        if raw_xlec:
            m = re.search(rf'{param}\[([-\d.]+)\]', raw_xlec, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
        raw_csq = data.get('raw_csq', '')
        if param == 'rsrp':
            csq_data = parse_at_csq(raw_csq)
            return csq_data.get('rssi', '-')
        return '-'

    def _update_speed_graph(self) -> None:
        rx, tx, ts = self._stat.last_n(60)
        if self._speed_graph:
            if rx:
                self._speed_graph.update_data('download', rx)
            if tx:
                self._speed_graph.update_data('upload', tx)
            self._speed_graph.refresh()
        prx = self._stat.peak_rx
        ptx = self._stat.peak_tx
        self._peak_rx_lbl.configure(text=f"{t('Download')} ↓: {prx:.2f} Mbps")
        self._peak_tx_lbl.configure(text=f"{t('Upload')} ↑: {ptx:.2f} Mbps")

    def _update_tower_history(self) -> None:
        data = self._signal_data
        if not data:
            return
        rsrp = self._extract_param("rsrp")
        sinr = self._extract_param("sinr")
        band = self._parse_active_band(data.get('raw_xlec', ''), data.get('raw_xact', ''))
        operator = parse_at_cops(data.get('raw_cops', ''))
        raw_cereg = data.get('raw_cereg', '')
        cell_id = '-'
        if raw_cereg:
            m = re.search(r'CEREG:\s*\d+,\d+,"?([^",]+)', raw_cereg)
            if m:
                cid = m.group(1)
                try:
                    cell_id = str(int(cid, 16))
                except ValueError:
                    cell_id = cid
        now_str = datetime.now().strftime('%H:%M:%S')
        if cell_id != self._prev_cell_id and cell_id != '-':
            entry = {
                'time': now_str,
                'cell_id': cell_id,
                'rsrp': str(rsrp),
                'sinr': str(sinr),
                'band': band,
                'operator': operator,
            }
            self._tower_history.add(entry)
            self._prev_cell_id = cell_id
            self._refresh_tower_tree()

    def _refresh_tower_tree(self) -> None:
        for item in self._tower_tree.get_children():
            self._tower_tree.delete(item)
        for entry in reversed(self._tower_history.entries):
            self._tower_tree.insert('', 'end', values=(
                entry.get('time', ''),
                entry.get('cell_id', ''),
                entry.get('rsrp', ''),
                entry.get('sinr', ''),
                entry.get('band', ''),
                entry.get('operator', ''),
            ))

    def _check_alert(self) -> None:
        if not self._alert_enabled:
            return
        data = self._signal_data
        if not data:
            return
        rsrp = self._extract_param("rsrp")
        if rsrp == '-':
            return
        try:
            rsrp_val = float(rsrp)
        except (ValueError, TypeError):
            return
        now = time.time()
        if rsrp_val < self._alert_threshold and (now - self._last_alert_time) > 30:
            self._last_alert_time = now
            msg = t("RSRP упал ниже {threshold} dBm!\nТекущее значение: {val} dBm").format(
                threshold=self._alert_threshold, val=rsrp_val)
            if self._alert_sound:
                with contextlib.suppress(Exception):
                    print('\a', end='')
            if self._alert_popup:
                self._safe_after(0, lambda: mb.showwarning(t("⚠ Алерт сигнала"), msg))

    def _clear_peaks(self) -> None:
        self._stat.reset_peaks()

    def _force_refresh(self) -> None:
        if self._api.is_connected:
            threading.Thread(target=self._collect_data, daemon=True).start()

    def _reboot_router(self) -> None:
        if not self._api.is_connected:
            return
        if mb.askyesno(t("Перезагрузить роутер"), t("Роутер будет перезагружен.\nПродолжить?")):
            try:
                self._api.cmd('/system/reboot')
                mb.showinfo(t("Перезагрузить роутер"),
                           t("Команда перезагрузки отправлена.\nРоутер перезагрузится через несколько секунд."))
                self._monitor_running = False
                self._api.disconnect()
                self._connect_btn.configure(text=t("🚀 Подключиться"))
                self._conn_status.configure(text=t("Отключено"), foreground="gray")
            except Exception as e:
                mb.showerror("Error", str(e))

    def _on_disconnect(self) -> None:
        self._conn_status.configure(text=t("Таймаут API..."), foreground="red")
        self._connect_btn.configure(text=t("🚀 Подключиться"))
        self._destroy_tray()
        reconnect_delay = 5
        for d in range(reconnect_delay, 0, -1):
            self._conn_status.configure(text=t("Переподключение через {d:.0f}с...").format(d=d))
            time.sleep(1)
        if not self._monitor_running:
            return
        try:
            self._api.connect()
            self._monitor_running = True
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
            self._create_tray()
            self._connect_btn.configure(text=t("⏹ Отключиться"))
            self._conn_status.configure(text=t("Подключено"), foreground="green")
            self._after_id = self._safe_after(int(self._monitor_interval * 1000), self._update_ui)
        except Exception:
            self._on_disconnect()

    def _stop_monitor(self) -> None:
        self._monitor_running = False
        if self._after_id is not None:
            with contextlib.suppress(Exception):
                self._root.after_cancel(self._after_id)
        self._monitor_thread = None

    def _create_tray(self) -> None:
        if pystray is None or self._tray_icon is not None:
            return
        try:
            img = Image.new('RGBA', (64, 64), (0, 120, 215, 255))
            draw = ImageDraw.Draw(img)
            draw.ellipse([8, 8, 56, 56], fill=(255, 255, 255, 255))
            menu = pystray.Menu(
                pystray.MenuItem("Show", lambda: self._safe_after(0, self._show_window)),
                pystray.MenuItem("Exit", lambda: self._safe_after(0, self._quit)),
            )
            self._tray_icon = pystray.Icon(APP_NAME, img, APP_NAME, menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
        except Exception as e:
            logger.warning("Tray creation failed: %s", e)

    def _destroy_tray(self) -> None:
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    def _show_window(self) -> None:
        self._root.deiconify()
        self._root.lift()

    def _on_close(self) -> None:
        if self._tray_icon is not None:
            self._root.withdraw()
        else:
            self._quit()

    def _quit(self) -> None:
        self._monitor_running = False
        self._destroy_tray()
        self._api.disconnect()
        self._root.destroy()

    def _check_updates(self) -> None:
        def check() -> None:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            try:
                req = urllib.request.Request(url, headers={'User-Agent': APP_NAME, 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    latest = data.get('tag_name', '')
                    if latest and latest.lstrip('v') > VERSION:
                        self._root.after(0, lambda: self._statusbar.configure(
                            text=f"Update {latest} available: {GITHUB_REPO}/releases"))
            except Exception:
                pass
        threading.Thread(target=check, daemon=True).start()

    def run(self) -> None:
        self._root.mainloop()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler("mikrotik4gmon.log", encoding='utf-8'),
            logging.StreamHandler(),
        ],
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=f"{APP_NAME} v{VERSION}")
    p.add_argument('--host', default='192.168.88.1', help='Router IP')
    p.add_argument('--port', type=int, default=API_PORT_DEFAULT, help='API port')
    p.add_argument('--password', default=DEFAULT_PASSWORD, help='API password')
    p.add_argument('--interval', type=float, default=MONITOR_INTERVAL, help='Update interval')
    p.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    p.add_argument('--dark', action='store_true', help='Dark mode')
    p.add_argument('--lang', default='ru', help='Language (ru/en)')
    return p.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    app = Application()
    app.run()


if __name__ == '__main__':
    main()
