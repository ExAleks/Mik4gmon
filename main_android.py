# -*- coding: utf-8 -*-
"""Mik4gmon — Android Kivy UI."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import (
    BooleanProperty,
    ColorProperty,
    DictProperty,
    ListProperty,
    NumericProperty,
    ObjectProperty,
    StringProperty,
)
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

import mikrotik_monitor as mtm
from core import (
    ALL_LTE_BANDS,
    BAND_FREQ_MAP,
    evaluate_signal,
    format_rate_mbps,
    parse_at_cops,
    parse_at_csq,
    parse_at_signal,
    t,
)

KV = """
<SignalLabel@Label>:
    font_size: '14sp'
    size_hint_y: None
    height: dp(30)

<ValueLabel@Label>:
    font_size: '16sp'
    bold: True
    size_hint_y: None
    height: dp(30)
    color: 0.2, 0.8, 0.2, 1

<ConnectScreen>:
    BoxLayout:
        orientation: 'vertical'
        padding: dp(20)
        spacing: dp(10)
        BoxLayout:
            size_hint_y: None
            height: dp(60)
            Label:
                text: 'Mik4gmon v' + app.version
                font_size: '24sp'
                bold: True
        Label:
            text: 'Router IP:'
            size_hint_y: None
            height: dp(20)
        TextInput:
            id: host
            text: '192.168.88.1'
            multiline: False
            size_hint_y: None
            height: dp(40)
        Label:
            text: 'Password:'
            size_hint_y: None
            height: dp(20)
        TextInput:
            id: pwd
            text: '1'
            password: True
            multiline: False
            size_hint_y: None
            height: dp(40)
        Label:
            text: 'LTE interface:'
            size_hint_y: None
            height: dp(20)
        TextInput:
            id: iface
            text: 'LTE1'
            multiline: False
            size_hint_y: None
            height: dp(40)
        Label:
            text: 'API Port:'
            size_hint_y: None
            height: dp(20)
        TextInput:
            id: port
            text: '8728'
            multiline: False
            size_hint_y: None
            height: dp(40)
        Button:
            id: connect_btn
            text: 'Connect'
            size_hint_y: None
            height: dp(50)
            background_color: 0, 0.5, 0.8, 1
            on_release: app.do_connect()
        Label:
            id: status
            text: 'Disconnected'
            font_size: '14sp'
            color: 0.7, 0.7, 0.7, 1

<MonitorScreen>:
    BoxLayout:
        orientation: 'vertical'
        ActionBar:
            size_hint_y: None
            height: dp(50)
            ActionView:
                use_separator: True
                ActionPrevious:
                    title: 'Mik4gmon'
                    with_previous: False
                    on_release: app.sm.current = 'connect'
                ActionButton:
                    text: 'Scan'
                    on_release: app.open_scan()
                ActionButton:
                    text: 'Bands'
                    on_release: app.open_bands()
                ActionButton:
                    text: 'History'
                    on_release: app.open_history()
        ScrollView:
            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: dp(900)
                padding: dp(10)
                spacing: dp(5)
                SignalLabel:
                    text: 'RSRP:'
                ValueLabel:
                    id: rsrp
                    text: '-'
                SignalLabel:
                    text: 'SINR:'
                ValueLabel:
                    id: sinr
                    text: '-'
                SignalLabel:
                    text: 'RSRQ:'
                ValueLabel:
                    id: rsrq
                    text: '-'
                SignalLabel:
                    text: 'RSSI:'
                ValueLabel:
                    id: rssi
                    text: '-'
                SignalLabel:
                    text: 'Band:'
                ValueLabel:
                    id: band
                    text: '-'
                SignalLabel:
                    text: 'Operator:'
                ValueLabel:
                    id: operator
                    text: '-'
                SignalLabel:
                    text: 'Cell ID:'
                ValueLabel:
                    id: cell_id
                    text: '-'
                SignalLabel:
                    text: 'RAT:'
                ValueLabel:
                    id: rat
                    text: '-'
                SignalLabel:
                    text: 'CA:'
                ValueLabel:
                    id: aggr
                    text: '-'
                SignalLabel:
                    text: 'Speed:'
                BoxLayout:
                    size_hint_y: None
                    height: dp(150)
                    SpeedGraph:
                        id: speed_graph
                BoxLayout:
                    size_hint_y: None
                    height: dp(30)
                    Label:
                        text: 'D: -'
                        id: peak_rx
                    Label:
                        text: 'U: -'
                        id: peak_tx

<ScanScreen>:
    name: 'scan'
    BoxLayout:
        orientation: 'vertical'
        ActionBar:
            size_hint_y: None
            height: dp(50)
            ActionView:
                ActionPrevious:
                    title: 'Cell Scan'
                    on_release: app.sm.current = 'monitor'
                ActionButton:
                    text: 'Scan'
                    on_release: app.do_scan()
        Label:
            id: scan_status
            text: ''
            size_hint_y: None
            height: dp(30)
        ScrollView:
            GridLayout:
                id: scan_grid
                cols: 1
                size_hint_y: None
                height: dp(1000)
                spacing: dp(2)

<BandScreen>:
    name: 'bands'
    BoxLayout:
        orientation: 'vertical'
        ActionBar:
            size_hint_y: None
            height: dp(50)
            ActionView:
                ActionPrevious:
                    title: 'Band Management'
                    on_release: app.sm.current = 'monitor'
                ActionButton:
                    text: 'Apply'
                    on_release: app.apply_bands()
        ScrollView:
            GridLayout:
                id: band_grid
                cols: 2
                size_hint_y: None
                height: dp(2000)
                spacing: dp(2)
        Label:
            id: band_info
            text: ''
            size_hint_y: None
            height: dp(30)

<HistoryScreen>:
    name: 'history'
    BoxLayout:
        orientation: 'vertical'
        ActionBar:
            size_hint_y: None
            height: dp(50)
            ActionView:
                ActionPrevious:
                    title: 'Tower History'
                    on_release: app.sm.current = 'monitor'
                ActionButton:
                    text: 'Clear'
                    on_release: app.clear_history()
        ScrollView:
            GridLayout:
                id: history_grid
                cols: 1
                size_hint_y: None
                height: dp(2000)
                spacing: dp(1)
"""


class SpeedGraph(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rx_data = deque(maxlen=60)
        self._tx_data = deque(maxlen=60)
        self._max_val = 1.0
        Clock.schedule_interval(self._update, 2.0)

    def add_data(self, rx: float, tx: float):
        self._rx_data.append(rx)
        self._tx_data.append(tx)
        mx = max(max(self._rx_data or [0]), max(self._tx_data or [0]))
        self._max_val = max(mx, 0.1)

    def _update(self, dt):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.12, 0.12, 0.18, 1)
            Rectangle(pos=self.pos, size=self.size)
            if len(self._rx_data) < 2:
                return
            w, h = self.size
            pad = 5
            Color(0, 0.47, 0.84, 1)
            pts = []
            n = len(self._rx_data)
            for i, v in enumerate(self._rx_data):
                x = pad + (w - 2 * pad) * (i / (n - 1))
                y = pad + (h - 2 * pad) * (v / self._max_val)
                pts.extend([x, y])
            if len(pts) >= 4:
                Line(points=pts, width=2)
            Color(0.91, 0.07, 0.14, 1)
            pts2 = []
            for i, v in enumerate(self._tx_data):
                x = pad + (w - 2 * pad) * (i / (n - 1))
                y = pad + (h - 2 * pad) * (v / self._max_val)
                pts2.extend([x, y])
            if len(pts2) >= 4:
                Line(points=pts2, width=2)


class ConnectScreen(Screen):
    pass


class MonitorScreen(Screen):
    pass


class ScanScreen(Screen):
    pass


class BandScreen(Screen):
    pass


class HistoryScreen(Screen):
    pass


class Mik4gmonApp(App):
    version = mtm.VERSION

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api = None
        self.iface_id = ''
        self.monitor_running = False
        self.stat = mtm.StatCollector(maxlen=300)
        self.tower_history: list[dict[str, str]] = []
        self.prev_cell_id = ''
        self.signal_data: dict[str, any] = {}
        self.band_vars: dict[str, any] = {}

    def build(self):
        self.sm = ScreenManager()
        self.sm.add_widget(ConnectScreen(name='connect'))
        self.sm.add_widget(MonitorScreen(name='monitor'))
        self.sm.add_widget(ScanScreen(name='scan'))
        self.sm.add_widget(BandScreen(name='bands'))
        self.sm.add_widget(HistoryScreen(name='history'))
        return self.sm

    def do_connect(self):
        cs = self.sm.get_screen('connect')
        host = cs.ids.host.text
        pwd = cs.ids.pwd.text
        iface = cs.ids.iface.text
        port_str = cs.ids.port.text
        try:
            port = int(port_str)
        except ValueError:
            cs.ids.status.text = 'Invalid port'
            return
        cs.ids.status.text = 'Connecting...'
        cs.ids.connect_btn.disabled = True
        threading.Thread(target=self._connect_thread, args=(host, port, pwd, iface), daemon=True).start()

    def _connect_thread(self, host, port, pwd, iface):
        try:
            self.api = mtm.RouterOSClient(host=host, port=port, password=pwd)
            self.api.connect()
            interfaces = self.api.raw_cmd('/interface/lte/print')
            for iface_obj in interfaces:
                if iface_obj.get('name', '').lower() == iface.lower():
                    self.iface_id = iface_obj.get('.id', iface)
                    break
            else:
                self.iface_id = iface
            self.monitor_running = True
            Clock.schedule_once(lambda dt: self._on_connected(), 0)
            threading.Thread(target=self._monitor_loop, daemon=True).start()
        except Exception as e:
            Clock.schedule_once(lambda dt: self._on_connect_error(str(e)), 0)

    def _on_connected(self):
        cs = self.sm.get_screen('connect')
        cs.ids.status.text = 'Connected'
        cs.ids.connect_btn.disabled = False
        self.sm.current = 'monitor'
        Clock.schedule_interval(self._update_ui, 2.0)

    def _on_connect_error(self, err):
        cs = self.sm.get_screen('connect')
        cs.ids.status.text = f'Error: {err}'
        cs.ids.connect_btn.disabled = False

    def _monitor_loop(self):
        while self.monitor_running and self.api and self.api.is_connected:
            try:
                self._collect_data()
            except Exception:
                pass
            time.sleep(2)

    def _collect_data(self):
        if not self.api or not self.api.is_connected:
            return
        data = {}
        try:
            r = self.api.raw_cmd('/interface/lte/print')
            for iface_obj in r:
                if iface_obj.get('name', '').lower() == self.sm.get_screen('connect').ids.iface.text.lower():
                    break
        except Exception:
            pass
        try:
            with mtm._AT_LOCK:
                raw_csq = self._do_at("at+csq")
                raw_xlec = self._do_at("at+xlec?")
                raw_xact = self._do_at("at+xact?")
                raw_cops = self._do_at("at+cops?")
                raw_cereg = self._do_at("at+cereg?")
            data['raw_csq'] = raw_csq
            data['raw_xlec'] = raw_xlec
            data['raw_xact'] = raw_xact
            data['raw_cops'] = raw_cops
            data['raw_cereg'] = raw_cereg
        except Exception:
            pass
        try:
            r = self.api.raw_cmd('/interface/monitor-traffic', f'=.id={self.iface_id}', '=once')
            if r:
                rx = int(r[0].get('rx-bits-per-second', 0)) / 1_000_000
                tx = int(r[0].get('tx-bits-per-second', 0)) / 1_000_000
                self.stat.add(rx, tx)
        except Exception:
            pass
        self.signal_data = data

    def _do_at(self, cmd: str) -> str:
        try:
            r = self.api.cmd('/interface/lte/at-chat', f'=.id={self.iface_id}',
                            f'=input={cmd}', '=wait=yes')
            return r.get('recv', '')
        except Exception:
            return ''

    def _extract(self, param: str) -> str:
        data = self.signal_data
        if not data:
            return '-'
        raw_xlec = data.get('raw_xlec', '')
        if raw_xlec:
            m = re.search(rf'{param}\[([-\d.]+)\]', raw_xlec, re.IGNORECASE)
            if m:
                return m.group(1)
        return '-'

    def _update_ui(self, dt):
        if not self.monitor_running:
            return
        ms = self.sm.get_screen('monitor')
        data = self.signal_data
        if not data:
            return
        rsrp = self._extract('rsrp')
        sinr = self._extract('sinr')
        rsrq = self._extract('rsrq')
        rssi_val = parse_at_csq(data.get('raw_csq', '')).get('rssi', '-')
        operator = parse_at_cops(data.get('raw_cops', ''))
        raw_xlec = data.get('raw_xlec', '')
        raw_xact = data.get('raw_xact', '')
        raw_cereg = data.get('raw_cereg', '')
        ms.ids.rsrp.text = f'{rsrp} dBm' if rsrp != '-' else '-'
        ms.ids.sinr.text = f'{sinr} dB' if sinr != '-' else '-'
        ms.ids.rsrq.text = f'{rsrq} dB' if rsrq != '-' else '-'
        ms.ids.rssi.text = f'{rssi_val} dBm' if rssi_val != '-' else '-'
        band = self._parse_band(raw_xlec, raw_xact)
        ms.ids.band.text = band
        ms.ids.operator.text = operator if operator else '-'
        cell_id = '-'
        if raw_cereg:
            m = re.search(r'CEREG:\s*\d+,\d+,"?([^",]+)', raw_cereg)
            if m:
                try:
                    cell_id = str(int(m.group(1), 16))
                except ValueError:
                    cell_id = m.group(1)
        ms.ids.cell_id.text = cell_id
        rat = '-'
        if raw_cereg:
            m = re.search(r'CEREG:\s*\d+,\d+,[^,]*,(\d+)', raw_cereg)
            if m:
                rat_map = {'7': 'LTE', '9': 'LTE-A'}
                rat = rat_map.get(m.group(1), f'RAT:{m.group(1)}')
        ms.ids.rat.text = rat
        aggr = self._parse_ca(raw_xlec)
        ms.ids.aggr.text = aggr
        if cell_id != '-' and cell_id != self.prev_cell_id:
            self.tower_history.append({
                'time': datetime.now().strftime('%H:%M:%S'),
                'cell_id': cell_id,
                'rsrp': rsrp,
                'band': band,
                'operator': operator,
            })
            self.prev_cell_id = cell_id
        rx, tx, _ = self.stat.last_n(60)
        speed_graph = ms.ids.speed_graph
        if rx and tx:
            speed_graph.add_data(rx[-1], tx[-1])
        ms.ids.peak_rx.text = f'D: {self.stat.peak_rx:.2f} Mbps'
        ms.ids.peak_tx.text = f'U: {self.stat.peak_tx:.2f} Mbps'

    def _parse_band(self, raw_xlec: str, raw_xact: str) -> str:
        if raw_xlec:
            for line in raw_xlec.split('\n'):
                line = line.strip()
                if line.startswith('XLEC'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        fields = parts[1].strip().split(',')
                        if len(fields) >= 5 and fields[4].strip() != '0':
                            return f"B{fields[4].strip()}"
        if raw_xact:
            m = re.search(r'XACT:\s*(\d+)', raw_xact)
            if m:
                mask = int(m.group(1))
                for bn in sorted(ALL_LTE_BANDS, reverse=True):
                    bm = mtm._STANDARD_TO_FIBOCOM.get(f"B{bn}", 0)
                    if bm and mask & bm:
                        return f"B{bn}"
        return '-'

    def _parse_ca(self, raw_xlec: str) -> str:
        if not raw_xlec:
            return '-'
        for line in raw_xlec.split('\n'):
            line = line.strip()
            if line.startswith('XLEC'):
                parts = line.split(':')
                if len(parts) >= 2:
                    fields = parts[1].strip().split(',')
                    if fields and fields[0].strip() == '0':
                        return '-'
        return 'CA'

    def do_scan(self):
        ss = self.sm.get_screen('scan')
        ss.ids.scan_status.text = 'Scanning...'
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        cells = []
        try:
            with mtm._AT_LOCK:
                raw = self._do_at("at@errc:scan_result():10")
            lines = raw.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or line.startswith('@') or 'scan_result' in line or '"ERROR"' in line:
                    continue
                parts = re.split(r'[,;()]+', line)
                entry = {}
                for part in parts:
                    part = part.strip()
                    kv = part.split(':', 1)
                    if len(kv) == 2:
                        k = kv[0].strip().lower()
                        v = kv[1].strip().strip('"').strip("'")
                        if k in ('earfcn', 'pci', 'rsrp', 'band', 'type'):
                            entry[k] = v
                if entry and 'earfcn' in entry:
                    cells.append(entry)
        except Exception:
            pass
        Clock.schedule_once(lambda dt: self._update_scan(cells), 0)

    def _update_scan(self, cells):
        ss = self.sm.get_screen('scan')
        grid = ss.ids.scan_grid
        grid.clear_widgets()
        if not cells:
            ss.ids.scan_status.text = 'Nothing found'
            return
        ss.ids.scan_status.text = f'Found: {len(cells)}'
        for cell in cells:
            text = f"EARFCN: {cell.get('earfcn','-')}  PCI: {cell.get('pci','-')}  RSRP: {cell.get('rsrp','-')}  Band: {cell.get('band','-')}"
            grid.add_widget(Label(text=text, size_hint_y=None, height=dp(30)))

    def open_scan(self):
        self.sm.current = 'scan'

    def open_bands(self):
        self.sm.current = 'bands'
        threading.Thread(target=self._load_bands, daemon=True).start()

    def _load_bands(self):
        current_mask = 0
        try:
            with mtm._AT_LOCK:
                raw = self._do_at("at+xact?")
            m = re.search(r'XACT:\s*(\d+)', raw)
            if m:
                current_mask = int(m.group(1))
        except Exception:
            pass
        Clock.schedule_once(lambda dt: self._populate_bands(current_mask), 0)

    def _populate_bands(self, current_mask: int):
        bs = self.sm.get_screen('bands')
        grid = bs.ids.band_grid
        grid.clear_widgets()
        self.band_vars.clear()
        for band_num in ALL_LTE_BANDS:
            key = f"B{band_num}"
            mask = mtm._STANDARD_TO_FIBOCOM.get(key, 0)
            if mask == 0:
                continue
            freq = BAND_FREQ_MAP.get(band_num, '?')
            active = bool(current_mask & mask)
            btn = ToggleButton(
                text=f"{key} ({freq} MHz)",
                size_hint_y=None,
                height=dp(40),
                state='down' if active else 'normal',
            )
            self.band_vars[key] = btn
            grid.add_widget(btn)

    def apply_bands(self):
        new_mask = 0
        for key, btn in self.band_vars.items():
            if btn.state == 'down':
                new_mask |= mtm._STANDARD_TO_FIBOCOM.get(key, 0)
        if new_mask == 0:
            bs = self.sm.get_screen('bands')
            bs.ids.band_info.text = 'No bands selected'
            return
        bs = self.sm.get_screen('bands')
        bs.ids.band_info.text = f'Sending: mask={new_mask}'
        threading.Thread(target=self._apply_bands_thread, args=(new_mask,), daemon=True).start()

    def _apply_bands_thread(self, mask: int):
        try:
            with mtm._AT_LOCK:
                self._do_at(f"at+xact={mask}")
            Clock.schedule_once(lambda dt: self.sm.get_screen('bands').ids.band_info.configure(text='Applied OK'), 0)
        except Exception as e:
            Clock.schedule_once(lambda dt: self.sm.get_screen('bands').ids.band_info.configure(text=f'Error: {e}'), 0)

    def open_history(self):
        self.sm.current = 'history'
        self._refresh_history()

    def _refresh_history(self):
        hs = self.sm.get_screen('history')
        grid = hs.ids.history_grid
        grid.clear_widgets()
        for entry in reversed(self.tower_history):
            text = f"{entry.get('time','')}  Cell: {entry.get('cell_id','')}  RSRP: {entry.get('rsrp','')}  Band: {entry.get('band','')}"
            grid.add_widget(Label(text=text, size_hint_y=None, height=dp(25)))

    def clear_history(self):
        self.tower_history.clear()
        self._refresh_history()

    def on_stop(self):
        self.monitor_running = False
        if self.api:
            self.api.disconnect()


if __name__ == '__main__':
    Mik4gmonApp().run()
