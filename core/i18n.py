"""Интернационализация (русский / английский)."""

from __future__ import annotations

LANGUAGES = {"ru": "Русский", "en": "English"}

_current_lang = "ru"

_EN: dict[str, str] = {
    "Отключено": "Disconnected",
    "Подключено": "Connected",
    "Ошибка": "Error",
    "Таймаут API...": "API timeout...",
    "Переподключение через {d:.0f}с...": "Reconnecting in {d:.0f}s...",
    "Подключитесь к роутеру": "Connect to the router",
    "🚀 Подключиться": "🚀 Connect",
    "⏹ Отключиться": "⏹ Disconnect",
    "📈 Монитор": "📈 Signal",
    "🗼 Вышка": "🗼 Tower",
    "📊 Состояние": "📊 Status",
    "🌐 Сеть": "🌐 Network",
    "⚙️ Подключение": "⚙️ Settings",
    "🛡 Белые списки (РФ)": "🛡 Whitelists (RU)",
    "Поверх окон": "Always on top",
    "🌙": "🌙",
    "IP роутера:": "Router IP:",
    "Пароль:": "Password:",
    "LTE интерфейс:": "LTE interface:",
    "Порт API:": "API port:",
    "Интервал обновления (с):": "Update interval (s):",
    "График:": "Graph:",
    "Очистить пики": "Clear peaks",
    "Перезагрузить роутер": "Reboot router",
    "Обновить данные": "Refresh data",
    "Соединение": "Connection",
    "Состояние": "Status",
    "Нет сигнала": "No signal",
    "Имя устройства (MikroTik)": "Device name (MikroTik)",
    "Серийный номер": "Serial number",
    "Модель": "Model",
    "Версия RouterOS": "RouterOS version",
    "Активные: {bands}": "Active: {bands}",
    "Отправлено: {codes}": "Sent: {codes}",
    "Ошибка: {err}": "Error: {err}",
    "Роутер будет перезагружен.\nПродолжить?": "The router will be rebooted.\nContinue?",
    "Команда перезагрузки отправлена.\nРоутер перезагрузится через несколько секунд.":
        "Reboot command sent.\nThe router will restart in a few seconds.",
    "Оператор (PLMN)": "Operator (PLMN)",
    "Рабочий Band (LTE)": "Active Band (LTE)",
    "Агрегация (CA)": "CA Aggregation",
    "Пик: {v}": "Peak: {v}",
    "последние {n} точек": "last {n} points",
    "Нет данных": "No data",
    "Системная информация": "System info",
    "Время работы": "Uptime",
    "Время сессии мониторинга": "Monitoring session time",
    "Нагрузка CPU": "CPU load",
    "Свободная память": "Free memory",
    "Всего памяти": "Total memory",
    "Скорость приёма (LTE)": "Receive rate (LTE)",
    "Скорость передачи (LTE)": "Transmit rate (LTE)",
    "Тип сети (RAT)": "Network type (RAT)",
    "Cell ID (Локальный сектор)": "Cell ID (local sector)",
    "⚠ Алерт при слабом сигнале": "⚠ Signal alert",
    "⚠ Алерт сигнала": "⚠ Signal Alert",
    "RSRP упал ниже {threshold} dBm!\nТекущее значение: {val} dBm":
        "RSRP dropped below {threshold} dBm!\nCurrent: {val} dBm",
    "История вышек": "Tower history",
    "Время": "Time",
    "Скан окружения": "Cell scan",
    "🔍 Сканировать": "🔍 Scan",
    "Сканирование...": "Scanning...",
    "Найдено: {n}": "Found: {n}",
    "Ничего не найдено": "Nothing found",
    "График скорости": "Speed graph",
    "Скорость": "Speed",
    "Download": "Download",
    "Upload": "Upload",
    "Отлично": "Excellent",
    "Хорошо": "Good",
    "Средне": "Fair",
    "Плохо": "Poor",
    "Критично": "Critical",
    "Ошибка подключения": "Connection error",
    "Связь с роутером не удалась:\n\n{err}": "Failed to connect:\n\n{err}",
    "Подключение к роутеру...": "Connecting...",
    "Управление бендами LTE": "LTE band management",
    "Применить": "Apply",
    "Выберите бенды для сканирования/выбора": "Select bands to scan/select",
}


def set_language(lang: str) -> None:
    global _current_lang
    if lang in LANGUAGES:
        _current_lang = lang


def current_language() -> str:
    return _current_lang


def available_languages() -> list[str]:
    return list(LANGUAGES.keys())


def t(text: str) -> str:
    if _current_lang == "ru":
        return text
    return _EN.get(text, text)
