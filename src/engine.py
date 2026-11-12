# -*- coding: utf-8 -*-
"""
Desktop Universal AI Usage Widget
- Полупрозрачный стеклянный фон, frameless, draggable
- Поддержка режимов: Claude, Antigravity (Gemini), Codex (ChatGPT), Grok Build
- Авто-синхронизация с Firefox / Claude Desktop в реальном времени (для Claude)
- Авто-чтение usage/cost из ~/.grok/sessions (для Grok)
- Ручной режим с авто-подсчетом из буфера обмена (для Antigravity, Codex и Grok)
- Система оповещений (алармов) при остатке 25% сессии (75% использовано) и 10% недели (90% использовано)
- Трей-интеграция, нативные звуки Windows (winsound) и настройки лимитов
"""
import sys
import os
import json
import time
import re
import shutil
import sqlite3
import csv
import urllib.request
import urllib.error
import subprocess
import base64
import argparse
import winsound
from datetime import datetime, timezone
import win32crypt
from Crypto.Cipher import AES
import websocket

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import Qt, QTimer, QPoint, QRectF, pyqtSignal, QThread
    from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
    from PyQt6.QtWidgets import QGraphicsOpacityEffect
    from PyQt6.QtGui import (QFont, QColor, QPainter, QPen, QBrush, QPixmap,
                              QLinearGradient, QAction, QPainterPath, QIntValidator)
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QMenu, QMessageBox, QFrame, QDialog,
        QLineEdit, QPushButton, QSlider, QCheckBox,
        QSystemTrayIcon, QStyle, QComboBox, QStackedWidget
    )
except Exception as e:
    with open(os.path.join(os.environ.get('APPDATA',''), 'AIWidgetDock', 'engine', 'error.txt'), "w", encoding="utf-8") as f:
        f.write(f"Ошибка импорта PyQt6: {e}\n")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════════════════
#  ПАРСИНГ АРГУМЕНТОВ И НАСТРОЙКА ТАРГЕТОВ
# ════════════════════════════════════════════════════════════════════════════
def log_debug(msg):
    try:
        from datetime import datetime
        with open(os.path.join(os.environ.get('APPDATA',''), 'AIWidgetDock', 'engine', 'engine_debug.log'), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

parser = argparse.ArgumentParser(description="Universal AI Usage Widget")
# Поддерживаем оба варианта: --widget-target (новый) и --target (в ярлыках)
parser.add_argument("--widget-target", dest="target", choices=["claude", "antigravity", "codex", "grok"], default=None)
parser.add_argument("--target", dest="target_alt", choices=["claude", "antigravity", "codex", "grok"], default=None)
parser.add_argument("--silent", action="store_true", help="Start minimized to tray")
args, unknown = parser.parse_known_args()
# Приоритет: --widget-target > --target > default "claude"
TARGET = args.target or args.target_alt or "claude"

TARGETS_CONFIG = {
    "claude": {
        "title": "⚡ Claude Usage",
        "config_file": "widget_config_claude.json",
        "session_bar_colors": ("#667eea", "#764ba2"),  # Фиолетовый градиент
        "weekly_bar_colors": ("#00c6ff", "#0072ff"),   # Синий градиент
        "session_limit": 1000000,
        "weekly_limit": 1900000,
        "enable_online": True,
        "session_label": "Текущая сессия",
        "weekly_label": "Лимит сообщений (5ч)",
    },
    "antigravity": {
        "title": "🪐 Antigravity Usage",
        "config_file": "widget_config_antigravity.json",
        "session_bar_colors": ("#ec008c", "#fc6767"),  # Розово-красный градиент
        "weekly_bar_colors": ("#1488c6", "#2b32b2"),   # Глубокий синий градиент
        "session_limit": 100,
        "weekly_limit": 100,
        "enable_online": True,
        "session_label": "Gemini 5-Hour",
        "weekly_label": "Gemini Weekly",
    },
    "codex": {
        "title": "💻 Codex Usage",
        "config_file": "widget_config_codex.json",
        "session_bar_colors": ("#11998e", "#38ef7d"),  # Изумрудный градиент
        "weekly_bar_colors": ("#00b4db", "#0083b0"),   # Голубой градиент
        "session_limit": 128000,
        "weekly_limit": 1280000,
        "enable_online": True,
        "session_label": "Лимит контекста",
        "weekly_label": "Лимит сообщений (5ч)",
    },
    "grok": {
        "title": "✦ Grok Usage",
        "config_file": "widget_config_grok.json",
        "session_bar_colors": ("#374151", "#9ca3af"),  # серый — контекст
        "weekly_bar_colors": ("#a78bfa", "#7c3aed"),   # фиолетовый — деньги/токены
        "session_limit": 500000,       # grok-4.5 context_window (не деньги)
        "weekly_limit": 50000000,      # мягкий ориентир; у SuperGrok лимиты большие
        "enable_online": True,
        "budget_alerts": False,        # не красить в красный «как будто сгорел»
        "session_label": "Контекст окна (не $)",
        "weekly_label": "Потрачено в сессии (счётчик)",
    },
}

TARGET_CFG = TARGETS_CONFIG[TARGET]
BASE_DIR = os.path.join(os.environ.get('APPDATA',''), 'AIWidgetDock', 'engine')
os.makedirs(BASE_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(BASE_DIR, TARGET_CFG["config_file"])


def _env_path(env_name, *parts):
    base = os.environ.get(env_name)
    if not base:
        if env_name == "APPDATA":
            base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        else:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, *parts)


def _first_existing_path(paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def _jwt_claim_account_id(token):
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        claims = json.loads(data.decode("utf-8"))
        auth_claims = claims.get("https://api.openai.com/auth", {})
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    except Exception:
        pass
    return None


def is_process_running(process_name):
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # 0x08000000 = CREATE_NO_WINDOW
        out = subprocess.check_output(
            ["tasklist", "/NH", "/FI", f"IMAGENAME eq {process_name}"],
            startupinfo=startupinfo,
            creationflags=0x08000000
        ).decode('utf-8', errors='ignore')
        return process_name.lower() in out.lower()
    except Exception:
        return False


# Размеры виджета
CODEX_PROCESS_NAMES = {
    "chatgpt.exe",
    "codex.exe",
    "openai.codex.exe",
}


def codex_desktop_running():
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            startupinfo=startupinfo,
            creationflags=0x08000000
        ).decode("utf-8", errors="ignore")
        for row in csv.reader(out.splitlines()):
            if row and row[0].strip().lower() in CODEX_PROCESS_NAMES:
                return True
    except Exception:
        try:
            out = subprocess.check_output(
                "tasklist",
                shell=True,
                creationflags=0x08000000
            ).decode("cp866", errors="ignore").lower()
            return any(name in out for name in CODEX_PROCESS_NAMES)
        except Exception:
            return False
    return False


def is_target_app_active(target_name):
    try:
        import win32gui
        import win32process
        import win32con
        import win32api
    except ImportError:
        return True

    if target_name == "claude":
        target_exes = {"claude.exe"}
    elif target_name == "antigravity":
        target_exes = {"antigravity.exe"}
    elif target_name == "codex":
        target_exes = {"chatgpt.exe", "codex.exe", "openai.codex.exe"}
    elif target_name == "grok":
        target_exes = {"grok.exe", "agent.exe"}
    else:
        return True

    found_active = [False]
    
    def enum_windows_callback(hwnd, extra):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.IsIconic(hwnd):
            return True
            
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid:
            try:
                handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    exe_name = win32process.GetModuleFileNameEx(handle, 0)
                    win32api.CloseHandle(handle)
                    base_exe = os.path.basename(exe_name).lower()
                    if base_exe in target_exes:
                        title = win32gui.GetWindowText(hwnd)
                        if title.strip():
                            found_active[0] = True
                            return False
            except Exception:
                pass
        return True

    try:
        win32gui.EnumWindows(enum_windows_callback, None)
    except Exception:
        pass
        
    return found_active[0]


def _target_is_foreground(target_name):
    try:
        import win32gui, win32process, win32con, win32api
        exes = {"claude": {"claude.exe"}, "antigravity": {"antigravity.exe"},
                "codex": {"chatgpt.exe", "codex.exe", "openai.codex.exe"},
                "grok": {"grok.exe", "agent.exe"}}.get(target_name, set())
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or win32gui.IsIconic(hwnd):
            return False
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        exe = os.path.basename(win32process.GetModuleFileNameEx(h, 0)).lower()
        win32api.CloseHandle(h)
        return exe in exes
    except Exception:
        return False


WIDGET_W = 340
WIDGET_H = 470 if TARGET == "antigravity" else 310

# Константы времени для ручного режима
SESSION_DURATION = 3 * 3600 + 3 * 60  # 3 часа 3 минуты
WEEKLY_DURATION = 7 * 24 * 3600      # 7 дней


# ════════════════════════════════════════════════════════════════════════════
#  СИНХРОНИЗАТОР (ФОНОВЫЙ ПОТОК ДЛЯ CLAUDE)
# ════════════════════════════════════════════════════════════════════════════
CDP_PROFILES_FILE = os.path.join(os.environ.get('APPDATA', ''), 'AIWidgetDock', 'engine', 'cdp_profiles.json')   # не во временной папке exe: список аккаунтов переживает перезапуск
os.makedirs(os.path.dirname(CDP_PROFILES_FILE), exist_ok=True)
CHROME_EXE = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe')) if os.path.exists(p)),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe")
CHROME_UD = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'User Data')


def load_cdp_profiles():
    """Список браузеров виджета: [{'port': 9222, 'profile': 'ClaudeWidget'}, ...]."""
    try:
        with open(CDP_PROFILES_FILE, encoding='utf-8') as f:
            lst = [x for x in json.load(f) if x.get('port') and x.get('profile')]
            if lst:
                return lst
    except Exception:
        pass
    base = [{'port': 9222, 'profile': 'ClaudeWidget'}, {'port': 9230, 'profile': 'ClaudeWidget2'}]   # оба браузера виджета, даже если файл списка потерян
    return [x for x in base if x['port'] == 9222 or os.path.isdir(os.path.join(CHROME_UD, x['profile']))]


def save_cdp_profiles(lst):
    with open(CDP_PROFILES_FILE, 'w', encoding='utf-8') as f:
        json.dump(lst, f, ensure_ascii=False, indent=1)


def launch_cdp_chrome(port, profile, visible=False):
    pos = '--window-position=120,80' if visible else '--window-position=-32000,-32000'
    subprocess.Popen([CHROME_EXE, f'--user-data-dir={os.path.join(CHROME_UD, profile)}', '--profile-directory=Default',
                      f'--remote-debugging-port={port}', '--remote-allow-origins=*', '--no-first-run',
                      '--no-default-browser-check', pos, '--window-size=560,800', 'https://claude.ai/new'],
                     creationflags=subprocess.CREATE_NO_WINDOW)


def cdp_fetch_json(port, path, timeout=8):
    """Запрос к claude.ai изнутри браузера виджета — свои куки и Cloudflare-допуск, без 403."""
    import websocket as _ws
    tabs = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2).read())
    tab = next((t for t in tabs if t.get('type') == 'page' and t.get('url', '').startswith('https://claude.ai')), None)
    if not tab:
        rq = urllib.request.Request(f'http://127.0.0.1:{port}/json/new?https://claude.ai/new', method='PUT')
        tab = json.loads(urllib.request.urlopen(rq, timeout=4).read())
        time.sleep(4)
    c = _ws.create_connection(tab['webSocketDebuggerUrl'], timeout=timeout)
    try:
        expr = ("fetch(%s,{credentials:'include'}).then(async r=>JSON.stringify({s:r.status,b:await r.text()}))"
                % json.dumps('https://claude.ai' + path))
        c.send(json.dumps({'id': 7, 'method': 'Runtime.evaluate',
                           'params': {'expression': expr, 'awaitPromise': True, 'returnByValue': True}}))
        while True:
            d = json.loads(c.recv())
            if d.get('id') == 7:
                break
    finally:
        c.close()
    v = json.loads(d['result']['result']['value'])
    if v['s'] != 200:
        raise urllib.error.HTTPError(path, v['s'], 'cdp', None, None)
    return json.loads(v['b'])


ANCHORS_FILE = os.path.join(os.environ.get('APPDATA', ''), 'AIWidgetDock', 'engine', 'reset_anchors.json')   # не во временной папке exe: иначе времена сброса терялись при каждом перезапуске


def _load_anchors():
    try:
        with open(ANCHORS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_reset_anchor(org, usage):
    """Запоминаем точное время сброса, когда оно пришло от claude.ai."""
    try:
        a = _load_anchors()
        e = a.setdefault(org, {})
        for key, src in (('five', 'five_hour'), ('week', 'seven_day')):
            v = (usage.get(src) or {}).get('resets_at')
            if v:
                e[key] = datetime.fromisoformat(v.replace('Z', '+00:00')).timestamp()
        with open(ANCHORS_FILE, 'w', encoding='utf-8') as f:
            json.dump(a, f)
    except Exception:
        pass


def estimate_resets(org):
    """(five_ts, week_ts) для файла Desktop: из сохранённых точных значений или по истории замеров."""
    now = time.time()
    a = _load_anchors().get(org, {})
    week = a.get('week')
    if week:
        while week <= now:
            week += 7 * 86400
    five = a.get('five') if a.get('five', 0) > now else None
    if not five:
        try:
            with open(os.path.join(os.path.expandvars(r'%APPDATA%\Claude'), 'plan-usage-history.json'), encoding='utf-8') as f:
                smp = [x for x in (json.load(f).get('samples') or []) if x.get('org') == org]
            start = None
            for i in range(len(smp) - 1, -1, -1):
                cur = smp[i]
                start = cur['t'] / 1000.0
                if i == 0:
                    break
                prv = smp[i - 1]
                if (cur['t'] - prv['t']) / 1000.0 > 5 * 3600 or (cur.get('u') or {}).get('fh', 0) < (prv.get('u') or {}).get('fh', 0):
                    break
            if start and start + 5 * 3600 > now and ((smp[-1].get('u') or {}).get('fh', 0) > 0):
                five = start + 5 * 3600
        except Exception:
            pass
    return five, week


def desktop_usage_sample():
    """Последний замер лимитов, который сам Claude Desktop пишет на диск (активный аккаунт)."""
    try:
        with open(os.path.join(os.path.expandvars(r'%APPDATA%\Claude'), 'plan-usage-history.json'), encoding='utf-8') as f:
            smp = json.load(f).get('samples') or []
        if smp:
            x = smp[-1]
            return x.get('org'), x.get('t', 0) / 1000.0, (x.get('u') or {})
    except Exception:
        pass
    return None, 0, {}


def timer_style(rem_sec, base="background: rgba(0,0,0,0.55); padding: 2px 5px; border-radius: 4px; font-weight: bold; font-size: 12px;"):
    """<60 мин салатовый, <20 мин ярко-зелёный, <5 мин мигает."""
    if rem_sec is None or rem_sec <= 0 or rem_sec >= 3600:
        col = "#FFB300"
    elif rem_sec < 300:
        col = "#00E676" if int(time.monotonic()) % 2 == 0 else "#1B5E20"
    elif rem_sec < 1200:
        col = "#00E676"
    else:
        col = "#C6FF00"
    return f"color: {col}; {base}"


def week_date_color(rem_sec, weekly_pct):
    """Дата недельного сброса: 100% — красная; первые сутки после сброса — зелёная;
    до сброса ≤2 дней — салатовая; иначе — обычная (None)."""
    if _pnum(weekly_pct) >= 100:
        return "#FF1744"
    if rem_sec is None or rem_sec <= 0:
        return None
    if rem_sec > 6 * 86400:
        return "#00E676"
    if rem_sec <= 2 * 86400:
        return "#C6FF00"
    return None


def recolor_label(lbl, col):
    base = getattr(lbl, '_base_ss', None)
    if base is None:
        base = lbl._base_ss = lbl.styleSheet()
    if col is None:
        ss = base
    elif re.search(r'(?<![-\w])color\s*:', base):
        ss = re.sub(r'(?<![-\w])color\s*:\s*[^;]+;', f'color: {col};', base, count=1)
    else:
        ss = base + f' color: {col};'
    if lbl.styleSheet() != ss:
        lbl.setStyleSheet(ss)


def desktop_active_org():
    """org_uuid аккаунта, под которым сейчас работает Claude Desktop.
    Источники: bridge-state.json (ключ 'org:...') и plan-usage-history.json
    (последний замер). Берётся тот, что записан позже."""
    base = os.path.expandvars(r'%APPDATA%\Claude')
    cands = []
    try:
        p = os.path.join(base, 'plan-usage-history.json')
        with open(p, encoding='utf-8') as f:
            smp = json.load(f).get('samples') or []
        if smp:
            cands.append((smp[-1].get('t', 0) / 1000.0, smp[-1].get('org')))
    except Exception:
        pass
    try:
        p = os.path.join(base, 'bridge-state.json')
        with open(p, encoding='utf-8') as f:
            keys = list(json.load(f).keys())
        if keys:
            cands.append((os.path.getmtime(p), keys[0].split(':')[0]))
    except Exception:
        pass
    cands = [c for c in cands if c[1]]
    return max(cands)[1] if cands else None


class ClaudeSyncWorker(QThread):
    session_status = pyqtSignal(bool, str, str)  # (is_valid, source_name, org_id)
    usage_fetched = pyqtSignal(dict)             # данные использования от API
    sessions_updated = pyqtSignal(list)          # список найденных сессий для меню
    context_updated = pyqtSignal(dict)
    diag_report = pyqtSignal(str)                # отчёт кнопки «Обновить сейчас»
    error_status = pyqtSignal(str)               # ошибка опроса активного аккаунта ('' = всё ок)

    def __init__(self):
        super().__init__()
        self.force_refresh = False
        self._report_pending = False
        self.scan_errors = []
        self.cdp_diag = []
        self.session_key = None
        self.org_id = None
        self.source_name = None
        self.is_running = True
        self.all_sessions = []
        self.selected_session_key = None
        self._last_bridge_start = 0.0
        self.context_path = ''
        self.context_auto = True
        self._last_context_check = 0.0

    def ensure_bridge(self):
        """Поднимает упавшие браузеры виджета (скрыто, без смены профиля)."""
        import socket
        if not hasattr(self, '_bridge_t'):
            self._bridge_t = {}
        for pr in load_cdp_profiles():
            port = pr['port']
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                    continue
            except OSError:
                pass
            if time.time() - self._bridge_t.get(port, 0) < 120:
                continue
            self._bridge_t[port] = time.time()
            try:
                launch_cdp_chrome(port, pr['profile'])
                log_debug(f'Claude: запущен браузер виджета порт {port}')
            except Exception as exc:
                log_debug(f'Claude: не удалось запустить браузер {port}: {exc}')

    def msleep(self, seconds):
        for _ in range(int(seconds * 10)):
            if not self.is_running:
                break
            time.sleep(0.1)

    def select_session(self, sk):
        self.selected_session_key = sk

    def run(self):
        last_scan_time = 0
        last_fetch_time = 0
        
        while self.is_running:
            now = time.time()
            if now - self._last_context_check >= 5:
                from cowork_context import read_context, latest_path
                self._last_context_check = now
                path = (latest_path() if self.context_auto else None) or self.context_path
                ctx = read_context(path)
                # Авто-режим: локальных чатов нет 30+ мин -> сейчас идёт облачный чат, старые цифры не показываем
                if self.context_auto and (not ctx or time.time() - ctx.get('mtime', 0) > 1800):
                    ctx = {'stale': True, 'title': ctx.get('title'), 'mtime': ctx.get('mtime')}
                self.context_updated.emit(ctx)
            
            if self.force_refresh:
                # Кнопка «Обновить сейчас»: полный пересбор аккаунтов и лимитов
                self.force_refresh = False
                self._report_pending = True
                self.scan_errors = []
                self.all_sessions = []
                self.session_key = None
                last_scan_time = 0
                log_debug('Claude: ручное обновление')

            # 1. Периодическое сканирование всех куки-файлов (каждые 30 секунд)
            _fast = os.path.exists(os.path.join(os.environ.get('TEMP', '.'), 'claude_widget_login.flag'))
            if now - last_scan_time >= (5 if (_fast or not self.all_sessions) else 30):   # без аккаунтов — раз в 5 с, а не каждую секунду (грузило процессор)
                last_scan_time = now
                self.ensure_bridge()
                found_raw = self.find_all_sessions()
                
                # Обновим наш список сессий
                new_sessions = []
                seen_keys = set()
                
                # Сначала перенесем уже известные валидные сессии
                for s in self.all_sessions:
                    still_exists = False
                    for raw in found_raw:
                        if raw['session_key'] == s['session_key']:
                            s['cf_clearance'] = raw.get('cf_clearance', '')
                            still_exists = True
                            break
                    if still_exists:
                        new_sessions.append(s)
                        seen_keys.add(s['session_key'])
                
                # Добавим новые сырые сессии
                for raw in found_raw:
                    sk = raw['session_key']
                    cf = raw.get('cf_clearance', '')
                    if sk not in seen_keys:
                        # Передаём cf_clearance чтобы пройти Cloudflare при проверке
                        org_id, email = self.get_session_info(sk, cf, raw.get('port'))
                        if org_id:
                            new_sessions.append({
                                'session_key': sk,
                                'cf_clearance': cf,
                                'source_name': raw['source_name'],
                                'port': raw.get('port'),
                                'org_id': org_id,
                                'email': email,
                                'utilization': 0.0,
                                'last_fetch': 0.0
                            })
                            seen_keys.add(sk)
                
                self.all_sessions = new_sessions
                # Отправляем обновленный список в UI
                ui_list = []
                for s in self.all_sessions:
                    ui_list.append({
                        'session_key': s['session_key'],
                        'email': s['email'],
                        'source_name': s['source_name']
                    })
                self.sessions_updated.emit(ui_list)
            
            # 2. Опрос лимитов для всех известных сессий (раз в 60 секунд)
            for s in self.all_sessions:
                # Если это активная сессия, опрашиваем её строго по общему графику
                is_active = (s['session_key'] == self.session_key)
                interval = 60 if is_active else 120  # Неактивные опрашиваем реже
                if now - s.get('last_fetch', 0) >= interval or (is_active and now - last_fetch_time >= 60):
                    try:
                        # Передаём cf_clearance для обхода Cloudflare 403
                        cf = s.get('cf_clearance', '')
                        usage_data = self.fetch_usage_limits(s['session_key'], s['org_id'], cf, s.get('port'))
                        s['last_fetch'] = time.time()

                        if usage_data:
                            five_hour = usage_data.get("five_hour", {})
                            util = float(five_hour.get("utilization", 0.0))
                            wk = float((usage_data.get("seven_day") or {}).get("utilization", 0.0) or 0.0)
                            prev = s.get('_prev_usage')
                            if prev is not None and (util > prev[0] or wk > prev[1]):
                                s['last_active'] = time.time()
                            s['_prev_usage'] = (util, wk)
                            s['utilization'] = util
                            s['last_error'] = ''
                            if is_active:
                                self.error_status.emit('')

                            save_reset_anchor(s['org_id'], usage_data)
                            if is_active:
                                self.session_status.emit(True, s['source_name'], s['org_id'])
                                self.usage_fetched.emit(usage_data)
                                log_debug('Claude: лимиты успешно обновлены')
                                last_fetch_time = time.time()
                    except urllib.error.HTTPError as he:
                        log_debug(f'Claude: ошибка лимитов HTTP {he.code}')
                        s['last_error'] = f'HTTP {he.code}' + (' — сессия истекла, нужен вход' if he.code == 401 else ' — отказ claude.ai/Cloudflare' if he.code == 403 else '')
                        if is_active:
                            self.error_status.emit(s['last_error'])
                        if he.code in (401, 403):
                            # 403 может быть временным (Cloudflare) — не удаляем сессию сразу
                            # Удаляем только при 401 (сессия протухла)
                            if he.code == 401:
                                if s['session_key'] == self.session_key:
                                    self.session_key = None
                                    self.org_id = None
                                    self.source_name = None
                                    self.session_status.emit(False, "", "")
                                if s in self.all_sessions:
                                    self.all_sessions.remove(s)
                                break
                            else:
                                # 403 — откладываем следующий опрос
                                s['last_fetch'] = time.time() - (interval - 30)
                    except Exception as exc:
                        log_debug(f'Claude: ошибка соединения {type(exc).__name__}')
                        s['last_error'] = f'нет соединения ({type(exc).__name__})'
                        if is_active:
                            self.error_status.emit(s['last_error'])
                        s['last_fetch'] = time.time() - (interval - 15)
            
            # 3. Автовыбор или применение ручного выбора активной сессии
            active_session = None
            search_pool = self.all_sessions
            if TARGET == "claude":
                # Приоритет: CDP > Claude Desktop > остальные
                cdp_sessions = [
                    s for s in self.all_sessions
                    if s.get("source_name", "").startswith("Chrome CDP")
                ]
                desktop_sessions = [
                    s for s in self.all_sessions
                    if s.get("source_name", "").startswith("Claude Desktop")
                ]
                if desktop_sessions:
                    search_pool = desktop_sessions
                elif cdp_sessions:
                    search_pool = cdp_sessions

            # Ручной выбор из меню важнее всего; иначе — аккаунт активного окна Claude Desktop
            if self.selected_session_key:
                for s in self.all_sessions:
                    if s['session_key'] == self.selected_session_key:
                        active_session = s
                        break
            if not active_session and TARGET == "claude":
                desk_org = desktop_active_org()
                if desk_org:
                    for s in self.all_sessions:
                        if s.get('org_id') == desk_org:
                            active_session = s
                            break

            desk_org_now = desktop_active_org() if TARGET == 'claude' else None
            if not active_session and desk_org_now and not self.selected_session_key:
                # Аккаунт Desktop не найден в браузерах — берём его цифры из файла Desktop, а не чужой аккаунт
                d_org, d_t, d_u = desktop_usage_sample()
                if d_org == desk_org_now and time.time() - d_t < 3 * 3600:
                    if self.session_key is not None:
                        self.session_key = self.org_id = None
                    if time.time() - getattr(self, '_desk_emit', 0) >= 30:
                        self._desk_emit = time.time()
                        self.source_name = 'Claude Desktop (файл)'
                        self.session_status.emit(True, 'Claude Desktop (файл)', d_org)
                        _f, _w = estimate_resets(d_org)
                        _iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat() if t else ''
                        self.usage_fetched.emit({'five_hour': {'utilization': d_u.get('fh', 0), 'resets_at': _iso(_f)},
                                                 'seven_day': {'utilization': d_u.get('sd', 0), 'resets_at': _iso(_w)}, '_file': True, '_ts': d_t})
                        self.error_status.emit('~' + time.strftime('%H:%M', time.localtime(d_t)))
                    if self._report_pending and last_scan_time:
                        self._report_pending = False
                        self.diag_report.emit(self._build_report(None))
                    self.msleep(1)
                    continue

            if not active_session and self.selected_session_key:
                for s in search_pool:
                    if s['session_key'] == self.selected_session_key:
                        active_session = s
                        break
            
            if not active_session and search_pool:
                # AUTO: account whose usage grew most recently = account in use now
                act = [s for s in search_pool if s.get('last_active')]
                if act:
                    active_session = max(act, key=lambda s: s['last_active'])
            if not active_session and search_pool:
                max_util = -1.0
                for s in search_pool:
                    util = s.get('utilization', 0.0)
                    if util > max_util:
                        max_util = util
                        active_session = s
                
                if max_util <= 0.0 and search_pool:
                    active_session = search_pool[0]
            
            if active_session:
                if active_session['session_key'] != self.session_key:
                    self.session_key = active_session['session_key']
                    self.org_id = active_session['org_id']
                    self.source_name = active_session['source_name']
                    active_session['last_fetch'] = 0.0
                    last_fetch_time = 0.0
            else:
                if self.session_key is not None:
                    self.session_key = None
                    self.org_id = None
                    self.source_name = None
                    self.session_status.emit(False, "", "")
            
            if self._report_pending and last_scan_time:
                self._report_pending = False
                self.diag_report.emit(self._build_report(active_session))

            self.msleep(1)

    def _build_report(self, active):
        lines = ['Браузеры виджета:'] + ['  ' + d for d in (self.cdp_diag or ['  нет данных'])]
        if self.scan_errors:
            lines += ['Ошибки:'] + ['  ' + e for e in self.scan_errors]
        lines.append(f'Найдено аккаунтов: {len(self.all_sessions)}')
        for x in self.all_sessions:
            st = x.get('last_error') or ('OK, 5ч {:.0f}%'.format(x.get('utilization', 0)) if x.get('last_fetch') else 'ещё не опрошен')
            lines.append(f"  • {x.get('email')} [{x.get('source_name')}] — {st}")
        desk = desktop_active_org()
        dm = next((x.get('email') for x in self.all_sessions if x.get('org_id') == desk), None)
        lines.append('Claude Desktop сейчас: ' + (dm or (f'аккаунт {desk[:8]}… НЕ найден среди браузеров виджета — войдите в него через «Войти в аккаунт»' if desk else 'не определён')))
        lines.append('Показываю: ' + (active.get('email') if active else 'НИЧЕГО'))
        return '\n'.join(lines)

    def stop(self):
        self.is_running = False

    def extract_ff_cookie(self, db_path):
        """Читает sessionKey и cf_clearance из Firefox cookies.sqlite.
        Возвращает (session_key, cf_clearance) или (None, None)."""
        temp_db = os.path.join(os.environ['TEMP'], 'claude_widget_ff_cookies.sqlite')
        try:
            shutil.copy2(db_path, temp_db)
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            # Забираем сразу оба нужных куки
            cursor.execute(
                "SELECT name, value FROM moz_cookies "
                "WHERE host LIKE '%claude.ai%' AND name IN ('sessionKey', 'cf_clearance')"
            )
            rows = cursor.fetchall()
            conn.close()
            if os.path.exists(temp_db):
                os.remove(temp_db)
            sk = cf = None
            for name, value in rows:
                if name == 'sessionKey':
                    sk = value
                elif name == 'cf_clearance':
                    cf = value
            return sk, cf
        except Exception:
            if os.path.exists(temp_db):
                try: os.remove(temp_db)
                except Exception: pass
        return None, None

    def extract_chrome_cookie(self, db_path, local_state_path):
        """Читает sessionKey и cf_clearance из Chrome/Edge/ClaudeDesktop cookies (v10/v11).
        Возвращает (session_key, cf_clearance) или (None, None).
        v20 (App-Bound Encryption, Chrome 127+) не поддерживается — используйте CDP."""
        temp_db = os.path.join(os.environ['TEMP'], 'claude_widget_chrome_cookies.db')
        try:
            shutil.copy2(db_path, temp_db)
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, encrypted_value FROM cookies "
                "WHERE host_key LIKE '%claude.ai%' AND name IN ('sessionKey', 'cf_clearance')"
            )
            rows = cursor.fetchall()
            conn.close()
            if os.path.exists(temp_db):
                os.remove(temp_db)
            if not rows:
                return None, None

            with open(local_state_path, 'r', encoding='utf-8') as f:
                local_state = json.load(f)
            enc_key = base64.b64decode(local_state['os_crypt']['encrypted_key'])[5:]
            dec_key = win32crypt.CryptUnprotectData(enc_key, None, None, None, 0)[1]

            def _decrypt(enc_val):
                prefix = enc_val[:3]
                if prefix in (b'v10', b'v11'):
                    iv = enc_val[3:15]
                    ciphertext = enc_val[15:]
                    cipher = AES.new(dec_key, AES.MODE_GCM, iv)
                    dec_val = cipher.decrypt(ciphertext)
                    text = dec_val.decode('utf-8', errors='ignore')
                    m = re.search(r'(sk-[A-Za-z0-9._-]+)', text)
                    if m:
                        return m.group(1)
                    return text.strip('\x00').strip() or None
                # v20 (App-Bound, Chrome 127+) — не расшифровываем без CDP
                return None

            sk = cf = None
            for name, enc_val in rows:
                val = _decrypt(enc_val)
                if val:
                    if name == 'sessionKey':
                        sk = val
                    elif name == 'cf_clearance':
                        cf = val
            return sk, cf
        except Exception:
            if os.path.exists(temp_db):
                try: os.remove(temp_db)
                except Exception: pass
        return None, None

    def find_cdp_sessions(self):
        """Читает sessionKey и cf_clearance через Chrome DevTools Protocol (CDP).
        Требует запуска Chrome с флагом --remote-debugging-port=9222.
        Обходит v20 App-Bound Encryption — куки возвращаются в открытом виде.
        Возвращает список словарей {'session_key', 'cf_clearance', 'source_name'}."""
        import urllib.request as _ur
        import json as _json
        sessions = []
        diag = []
        self.cdp_diag = diag
        cdp_ports = [9222, 9230]  # Проверяем несколько портов
        own = {x['port']: x['profile'] for x in load_cdp_profiles()}
        cdp_ports = list(own)
        for port in cdp_ports:
            try:
                # Защита: порт должен держать именно браузер виджета, а не чужой Chrome (WhatsApp и т.п.)
                try:
                    import psutil as _ps
                    pid = next((c.pid for c in _ps.net_connections('tcp')
                                if c.status == 'LISTEN' and c.laddr.port == port), None)
                    if pid:
                        args = _ps.Process(pid).cmdline()
                        udd = next((a.split('=', 1)[1].strip('"') for a in args if a.startswith('--user-data-dir=')), '')
                        if os.path.basename(udd.rstrip('\\/')) != own.get(port):
                            who = os.path.basename(udd) or (args[0] if args else '?')
                            diag.append(f'порт {port}: ЗАНЯТ ЧУЖОЙ программой ({who}) — пропускаю')
                            log_debug(f'Claude: порт {port} занят чужим процессом {pid} ({who})')
                            continue
                except ImportError:
                    pass
                # Получаем список вкладок/страниц
                with _ur.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as r:
                    tabs = _json.loads(r.read().decode())
                # Ищем вкладку с claude.ai или берём первую
                ws_url = None
                browser_name = f'Chrome CDP:{port}'
                for tab in tabs:
                    if tab.get('type') == 'page':
                        url = tab.get('url', '')
                        if 'claude.ai' in url:
                            ws_url = tab.get('webSocketDebuggerUrl')
                            break
                if not ws_url and tabs:
                    # Берём любую page-вкладку
                    for tab in tabs:
                        if tab.get('type') == 'page' and tab.get('webSocketDebuggerUrl'):
                            ws_url = tab.get('webSocketDebuggerUrl')
                            break
                if not ws_url:
                    # Пробуем /json/version для браузерного WebSocket
                    try:
                        with _ur.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=2) as r:
                            ver = _json.loads(r.read().decode())
                            ws_url = ver.get('webSocketDebuggerUrl')
                    except Exception:
                        pass
                if not ws_url:
                    diag.append(f'порт {port}: браузер без вкладок')
                    continue

                # Подключаемся по WebSocket и запрашиваем все куки
                import websocket as _ws  # websocket-client
                import threading as _th
                result = {'cookies': None, 'done': _th.Event()}

                def _on_msg(wsapp, msg):
                    try:
                        d = _json.loads(msg)
                        if d.get('id') == 1:
                            result['cookies'] = d.get('result', {}).get('cookies', [])
                            result['done'].set()
                    except Exception:
                        pass

                def _on_open(wsapp):
                    wsapp.send(_json.dumps({'id': 1, 'method': 'Network.getAllCookies'}))

                wsapp = _ws.WebSocketApp(ws_url, on_open=_on_open, on_message=_on_msg)
                t = _th.Thread(target=lambda: wsapp.run_forever(ping_timeout=3), daemon=True)
                t.start()
                result['done'].wait(timeout=4)
                wsapp.close()

                cookies = result.get('cookies') or []
                sk = cf = None
                for ck in cookies:
                    domain = ck.get('domain', '')
                    if 'claude.ai' in domain:
                        if ck.get('name') == 'sessionKey':
                            sk = ck.get('value')
                        elif ck.get('name') == 'cf_clearance':
                            cf = ck.get('value')
                diag.append(f'порт {port}: вход в claude.ai есть' if sk else f'порт {port}: браузер работает, но вход в claude.ai НЕ выполнен')
                if sk:
                    sessions.append({
                        'port': port,
                        'session_key': sk,
                        'cf_clearance': cf or '',
                        'source_name': browser_name
                    })
            except ImportError:
                # websocket-client не установлен — CDP недоступен
                break
            except Exception as e:
                diag.append(f'порт {port}: нет связи с браузером ({type(e).__name__})')
                continue
        return sessions

    def validate_session(self, sk):
        req = urllib.request.Request(
            "https://claude.ai/api/organizations",
            headers={
                "Cookie": f"sessionKey={sk}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                orgs = json.loads(r.read().decode())
                if isinstance(orgs, list) and len(orgs) > 0:
                    return orgs[0]['uuid']
        except Exception:
            pass
        return None

    def _build_headers(self, sk, cf_clearance=''):
        """Формирует заголовки HTTP-запроса, имитирующие браузер.
        Передаёт cf_clearance чтобы пройти Cloudflare без 403."""
        cookie = f"sessionKey={sk}"
        if cf_clearance:
            cookie += f"; cf_clearance={cf_clearance}"
        return {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://claude.ai/",
            "Origin": "https://claude.ai",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    def get_session_info(self, sk, cf_clearance='', port=None):
        if port:
            try:
                orgs = cdp_fetch_json(port, '/api/organizations')
                if isinstance(orgs, list) and orgs:
                    name = orgs[0].get('name', '')
                    return orgs[0]['uuid'], name.split("'s Organization")[0]
            except Exception as exc:
                log_debug(f'Claude: вход через браузер {port}: {type(exc).__name__} {getattr(exc, "code", "")}')
                self.scan_errors.append(f'порт {port}: проверка входа HTTP {getattr(exc, "code", type(exc).__name__)}')
        """Проверяет сессию через API organizations. Передаёт cf_clearance для обхода Cloudflare."""
        req = urllib.request.Request(
            "https://claude.ai/api/organizations",
            headers=self._build_headers(sk, cf_clearance)
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                orgs = json.loads(r.read().decode())
                if isinstance(orgs, list) and len(orgs) > 0:
                    org_id = orgs[0]['uuid']
                    org_name = orgs[0].get('name', '')
                    email = org_name
                    if "'s Organization" in org_name:
                        email = org_name.split("'s Organization")[0]
                    return org_id, email
        except Exception as exc:
            log_debug(f'Claude: проверка авторизации {type(exc).__name__}, HTTP {getattr(exc, "code", "-")}')
            self.scan_errors.append(f'проверка входа: {type(exc).__name__} HTTP {getattr(exc, "code", "-")}')
        return None, None

    def find_all_sessions(self):
        """Сканирует все браузеры и Claude Desktop в поисках sessionKey.
        Порядок приоритета: CDP (Chrome live) > Claude Desktop > Chrome > Firefox > Edge.
        Для каждой сессии сохраняет cf_clearance для обхода Cloudflare."""

        # ── 0. CDP (Chrome с --remote-debugging-port) — наивысший приоритет ──
        cdp_sessions = self.find_cdp_sessions()

        ff_candidates = []
        ff_dir = os.path.join(os.environ.get('APPDATA', ''), 'Mozilla', 'Firefox', 'Profiles')
        if os.path.exists(ff_dir):
            for profile in os.listdir(ff_dir):
                db_path = os.path.join(ff_dir, profile, 'cookies.sqlite')
                if os.path.exists(db_path):
                    ff_candidates.append((f"Firefox ({profile.split('.')[-1]})", db_path, False))

        chrome_candidates = []
        chrome_dir = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'User Data')
        if os.path.exists(chrome_dir):
            for root, dirs, files in os.walk(chrome_dir):
                if os.path.basename(root) == 'Network' and 'Cookies' in files:
                    profile = os.path.basename(os.path.dirname(root))
                    db_path = os.path.join(root, 'Cookies')
                    chrome_candidates.append((f"Chrome ({profile})", db_path, True))

        edge_candidates = []
        edge_dir = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Edge', 'User Data')
        if os.path.exists(edge_dir):
            for root, dirs, files in os.walk(edge_dir):
                if os.path.basename(root) == 'Network' and 'Cookies' in files:
                    profile = os.path.basename(os.path.dirname(root))
                    db_path = os.path.join(root, 'Cookies')
                    edge_candidates.append((f"Edge ({profile})", db_path, True))

        claude_candidates = []
        claude_store_dir = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Packages', 'Claude_pzs8sxrjxfjjc', 'LocalCache', 'Roaming', 'Claude')
        if os.path.exists(claude_store_dir):
            db_path = os.path.join(claude_store_dir, 'Network', 'Cookies')
            if os.path.exists(db_path):
                claude_candidates.append(("Claude Desktop", db_path, True))
        claude_std_dir = os.path.join(os.environ.get('APPDATA', ''), 'Claude')
        if os.path.exists(claude_std_dir):
            db_path = os.path.join(claude_std_dir, 'Network', 'Cookies')
            if os.path.exists(db_path):
                claude_candidates.append(("Claude Desktop (Std)", db_path, True))

        candidates = claude_candidates + chrome_candidates + ff_candidates + edge_candidates
        sessions = list(cdp_sessions)  # CDP-сессии идут первыми
        seen_keys = {s['session_key'] for s in sessions}

        for name, db_path, is_chrome in candidates:
            try:
                sk = cf = None
                if not is_chrome:
                    # Firefox — plain-text куки
                    sk, cf = self.extract_ff_cookie(db_path)
                else:
                    # Chrome/Edge/Claude Desktop — зашифрованные (v10/v11)
                    if "Claude" in name:
                        local_state_path = os.path.join(os.path.dirname(os.path.dirname(db_path)), 'Local State')
                    else:
                        local_state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(db_path))), 'Local State')
                    if local_state_path and os.path.exists(local_state_path):
                        sk, cf = self.extract_chrome_cookie(db_path, local_state_path)
                if sk and sk not in seen_keys:
                    seen_keys.add(sk)
                    sessions.append({
                        'session_key': sk,
                        'cf_clearance': cf or '',
                        'source_name': name
                    })
            except Exception:
                continue
        return sessions

    def fetch_usage_limits(self, sk, org_id, cf_clearance='', port=None):
        if port:
            return cdp_fetch_json(port, f'/api/organizations/{org_id}/usage')
        """Запрашивает лимиты использования. Передаёт cf_clearance для обхода Cloudflare 403."""
        url = f"https://claude.ai/api/organizations/{org_id}/usage"
        req = urllib.request.Request(
            url,
            headers=self._build_headers(sk, cf_clearance)
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())


class AntigravitySyncWorker(QThread):
    session_status = pyqtSignal(bool, str, str)  # (is_valid, source_name, org_id)
    usage_fetched = pyqtSignal(dict)             # данные использования от API

    def __init__(self):
        super().__init__()
        self.is_running = True
        self._codex_auth_path = _first_existing_path([
            os.path.join(os.path.expanduser("~"), ".codex", "auth.json"),
            _env_path("APPDATA", "Codex", "auth.json"),
            _env_path("LOCALAPPDATA", "Codex", "auth.json"),
        ])

    def msleep(self, seconds):
        for _ in range(int(seconds * 10)):
            if not self.is_running:
                break
            time.sleep(0.1)

    def _load_codex_auth(self):
        auth_paths = []
        if self._codex_auth_path:
            auth_paths.append(self._codex_auth_path)
        auth_paths.extend([
            os.path.join(os.path.expanduser("~"), ".codex", "auth.json"),
            _env_path("APPDATA", "Codex", "auth.json"),
            _env_path("LOCALAPPDATA", "Codex", "auth.json"),
        ])

        seen = set()
        for path in auth_paths:
            if not path or path in seen:
                continue
            seen.add(path)
            try:
                if not os.path.exists(path):
                    continue
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tokens = data.get("tokens", {}) if isinstance(data, dict) else {}
                access_token = tokens.get("access_token") or ""
                if not access_token:
                    continue
                account_id = tokens.get("account_id") or _jwt_claim_account_id(access_token) or _jwt_claim_account_id(tokens.get("id_token", ""))
                return {
                    "path": path,
                    "access_token": access_token,
                    "account_id": account_id,
                    "raw": data,
                }
            except Exception as e:
                print(f"Error reading Codex auth {path}: {e}")
        return None

    def _fetch_codex_usage(self, access_token, account_id=None):
        req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("User-Agent", "Codex Desktop/26.623.9142.0 (Windows NT 10.0; x64)")
        req.add_header("originator", "Codex Desktop")
        req.add_header("Accept", "application/json")
        req.add_header("Referer", "https://chatgpt.com/")
        if account_id:
            req.add_header("ChatGPT-Account-Id", account_id)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def run(self):
        while self.is_running:
            try:
                if TARGET == "codex":
                    if not codex_desktop_running():
                        self.session_status.emit(False, "", "")
                        self.msleep(2)
                        continue

                    auth = self._load_codex_auth()
                    if auth and auth.get("access_token"):
                        try:
                            usage_data = self._fetch_codex_usage(auth["access_token"], auth.get("account_id"))
                            if usage_data:
                                self.session_status.emit(True, "Codex", auth.get("account_id") or "")
                                self.usage_fetched.emit(usage_data)
                                self.msleep(15)
                                continue
                        except Exception as e:
                            print(f"Codex auth usage fetch failed: {e}")

                # Dynamically resolve port
                port = None
                port_file = None
                port_candidates = []
                if TARGET == "antigravity":
                    port_candidates = [
                        _env_path("APPDATA", "Antigravity", "DevToolsActivePort"),
                        _env_path("APPDATA", "Antigravity", "Default", "DevToolsActivePort"),
                    ]
                elif TARGET == "codex":
                    port_candidates = [
                        _env_path("APPDATA", "ai.opencode.desktop", "DevToolsActivePort"),
                        _env_path("LOCALAPPDATA", "Packages", "OpenAI.Codex_2p2nqsd0c76g0",
                                  "LocalCache", "Roaming", "Codex", "web", "Codex", "DevToolsActivePort"),
                        _env_path("LOCALAPPDATA", "Packages", "OpenAI.Codex_2p2nqsd0c76g0",
                                  "LocalCache", "Roaming", "Codex", "DevToolsActivePort"),
                        _env_path("APPDATA", "Codex", "DevToolsActivePort"),
                    ]

                port_file = _first_existing_path(port_candidates)
                if port_file:
                    try:
                        with open(port_file, "r", encoding="utf-8") as f:
                            lines = f.read().splitlines()
                            if lines:
                                port = int(lines[0])
                    except Exception as e:
                        print(f"Error reading port file {port_file}: {e}")
                
                if not port:
                    port = 9135 if TARGET == "antigravity" else 9136

                source_name = "Antigravity" if TARGET == "antigravity" else "Codex"

                # Check if app is alive by querying HTTP port
                url = f"http://127.0.0.1:{port}/json"
                req = urllib.request.Request(url)
                try:
                    with urllib.request.urlopen(req, timeout=3) as r:
                        targets = json.loads(r.read().decode())
                    app_alive = True
                except Exception:
                    app_alive = False

                if not app_alive:
                    self.session_status.emit(False, "", "")
                    self.msleep(5)
                    continue

                # App is alive! Show widget
                self.session_status.emit(True, source_name, "")

                # Try to get active conversation ID and calculate token size
                convo_id = None
                ws_url = None
                
                # First find the active page and its convo_id
                for t in targets:
                    if t.get('type') == 'page' and 'webSocketDebuggerUrl' in t:
                        page_url = t.get('url', '')
                        match = re.search(r'/c/([a-f0-9\-]{36})', page_url)
                        if match:
                            convo_id = match.group(1)
                            ws_url = t['webSocketDebuggerUrl']
                            break

                if not ws_url:
                    for t in targets:
                        if t.get('type') == 'page' and 'webSocketDebuggerUrl' in t:
                            ws_url = t['webSocketDebuggerUrl']
                            page_url = t.get('url', '')
                            match = re.search(r'/c/([a-f0-9\-]{36})', page_url)
                            if match:
                                convo_id = match.group(1)
                            break
                            
                print(f"[DEBUG] Found convo_id: {convo_id}, ws_url: {ws_url}", flush=True)
                chat_tokens = 0
                if convo_id:
                    try:
                        db_path = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "conversations", f"{convo_id}.db")
                        if os.path.exists(db_path):
                            print(f"[DEBUG] db_path exists: {db_path}", flush=True)
                            db_path_clean = db_path.replace('\\', '/')
                            db_uri = f"file:{db_path_clean}?mode=ro"
                            conn = sqlite3.connect(db_uri, uri=True)
                            cur = conn.cursor()
                            cur.execute("SELECT SUM(LENGTH(step_payload)) FROM steps")
                            val = cur.fetchone()[0]
                            conn.close()
                            if val:
                                chat_tokens = int(val) // 40
                                print(f"[DEBUG] Calculated chat_tokens: {chat_tokens}", flush=True)
                        else:
                            print(f"[DEBUG] db_path DOES NOT exist: {db_path}", flush=True)
                    except Exception as e:
                        print(f"Error reading sqlite chat tokens: {e}", flush=True)

                print(f"[DEBUG] Emitting chat_tokens: {chat_tokens}", flush=True)
                if not ws_url:
                    # Even if no websocket target is open, we still have SQLite tokens!
                    self.usage_fetched.emit({"chat_tokens": chat_tokens})
                    self.msleep(5)
                    continue

                # Connect via WebSocket and evaluate
                try:
                    ws = websocket.create_connection(ws_url, suppress_origin=True)
                    js_code = """
                    (async () => {
                      const rootEl = document.getElementById('root');
                      if (!rootEl) return { error: "No root element found" };
                      
                      const key = Object.keys(rootEl).find(k => k.startsWith('__reactContainer') || k.startsWith('__reactFiber'));
                      if (!key) return { error: "No React internal instance" };
                      
                      const fiber = rootEl[key];
                      
                      let foundNode = null;
                      const visited = new Set();
                      
                      let foundClient = null;
                      const walk = (node) => {
                        if (!node || visited.has(node) || foundClient) return;
                        visited.add(node);
                        
                        const props = node.memoizedProps;
                        // Antigravity перестал класть клиент в props.agentClient напрямую -
                        // теперь это значение React Context (обычно props.value), поэтому
                        // ищем любой проп-объект с методом retrieveUserQuotaSummary.
                        if (props && props.agentClient) {
                          foundNode = node;
                          foundClient = props.agentClient;
                          return;
                        }
                        if (props) {
                          for (const k of Object.keys(props)) {
                            const v = props[k];
                            if (v && typeof v === 'object' && typeof v.retrieveUserQuotaSummary === 'function') {
                              foundNode = node;
                              foundClient = v;
                              return;
                            }
                          }
                        }
                        
                        if (node.child) walk(node.child);
                        if (foundClient) return;
                        if (node.sibling) walk(node.sibling);
                      };
                      
                      walk(fiber);
                      
                      if (!foundClient) return { error: "Could not find node with agentClient" };
                      
                      const agentClient = foundClient;
                      try {
                        const quota = await agentClient.retrieveUserQuotaSummary();
                        
                        const serializeVal = (v) => {
                          if (v === null || v === undefined) return null;
                          if (typeof v === 'bigint') return v.toString();
                          if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') return v;
                          
                          if (typeof v === 'object') {
                            if (Array.isArray(v)) {
                              return v.map(serializeVal);
                            }
                            
                            const methods = Object.getOwnPropertyNames(Object.getPrototypeOf(v));
                            const hasGetters = methods.some(m => m.startsWith('get') && typeof v[m] === 'function');
                            
                            if (hasGetters) {
                              const res = {};
                              for (const m of methods) {
                                if (m.startsWith('get') && m !== 'get' && m !== 'constructor' && typeof v[m] === 'function') {
                                  try {
                                    const name = m.substring(3).replace(/^[A-Z]/, c => c.toLowerCase());
                                    res[name] = serializeVal(v[m]());
                                  } catch(e) {
                                    res[m] = { error: e.message };
                                  }
                                }
                              }
                              return res;
                            }
                            
                            const res = {};
                            for (const k of Object.keys(v)) {
                              res[k] = serializeVal(v[k]);
                            }
                            return res;
                          }
                          return typeof v;
                        };
                        
                        const serialized = serializeVal(quota) || {};
                        if (typeof agentClient.getTokenBase === 'function') {
                            try {
                                const tokenBase = await agentClient.getTokenBase();
                                if (tokenBase && typeof tokenBase.totalTokens === 'number') {
                                    serialized.active_chat_tokens = tokenBase.totalTokens;
                                }
                            } catch (err) {}
                        }
                        return serialized;
                      } catch (e) {
                        return { error: e.message };
                      }
                    })()
                    """
                    
                    payload = {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": js_code,
                            "returnByValue": True,
                            "awaitPromise": True
                        }
                    }
                    
                    ws.send(json.dumps(payload))
                    result = json.loads(ws.recv())
                    ws.close()
                    
                    quota_data = result.get("result", {}).get("result", {}).get("value", {})
                    if quota_data and "error" not in quota_data:
                        if "active_chat_tokens" in quota_data:
                            chat_tokens = int(quota_data["active_chat_tokens"])
                            print(f"[DEBUG] Using precise WebSocket tokens: {chat_tokens}", flush=True)
                        quota_data["chat_tokens"] = chat_tokens
                        self.usage_fetched.emit(quota_data)
                        self.msleep(2)
                    else:
                        self.usage_fetched.emit({"chat_tokens": chat_tokens})
                        self.msleep(2)
                except Exception:
                    self.usage_fetched.emit({"chat_tokens": chat_tokens})
                    self.msleep(2)
            except Exception:
                self.session_status.emit(False, "", "")
                self.msleep(2)

    def stop(self):
        self.is_running = False


class GrokSyncWorker(QThread):
    """Читает usage/cost из локальных сессий Grok Build (~/.grok/sessions)."""

    session_status = pyqtSignal(bool, str, str)
    usage_fetched = pyqtSignal(dict)

    # costUsdTicks → USD (эмпирически: ticks / 1e9)
    COST_TICKS_PER_USD = 1_000_000_000.0
    WEEKLY_SECONDS = 7 * 24 * 3600

    def __init__(self):
        super().__init__()
        self.is_running = True
        home = os.path.expanduser('~')
        self.grok_home = os.path.join(home, '.grok')
        self.sessions_root = os.path.join(self.grok_home, 'sessions')
        self.active_path = os.path.join(self.grok_home, 'active_sessions.json')

    def msleep(self, seconds):
        for _ in range(int(seconds * 10)):
            if not self.is_running:
                break
            time.sleep(0.1)

    def stop(self):
        self.is_running = False

    def _grok_running(self):
        return is_process_running('grok.exe') or is_process_running('agent.exe')

    def _context_window(self):
        """Реальный context_window модели из ~/.grok/models_cache.json (grok-4.5 = 500000)."""
        cache = os.path.join(self.grok_home, 'models_cache.json')
        try:
            if not os.path.exists(cache):
                return 500000
            with open(cache, 'r', encoding='utf-8') as f:
                data = json.load(f)
            models = data.get('models') or {}
            # prefer grok-4.5 / grok-4.5-build
            for key in ('grok-4.5', 'grok-4.5-build', 'grok-4', 'grok-3'):
                info = models.get(key) or {}
                if isinstance(info, dict):
                    meta = info.get('info') or info
                    cw = meta.get('context_window') or meta.get('contextWindow')
                    if cw:
                        return int(cw)
            for key, info in models.items():
                if not isinstance(info, dict):
                    continue
                meta = info.get('info') or info
                cw = meta.get('context_window') or meta.get('contextWindow')
                if cw:
                    return int(cw)
        except Exception as e:
            print(f'Grok context_window read failed: {e}')
        return 500000

    def _active_session_ids(self):
        ids = set()
        try:
            if os.path.exists(self.active_path):
                with open(self.active_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for row in data:
                        sid = row.get('session_id')
                        if sid:
                            ids.add(sid)
        except Exception:
            pass
        return ids

    def _parse_session_updates(self, updates_path):
        max_ctx = 0
        usages = []
        try:
            with open(updates_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'totalTokens' not in line and 'usage' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    def walk(node):
                        nonlocal max_ctx
                        if isinstance(node, dict):
                            usage = node.get('usage')
                            if isinstance(usage, dict) and (
                                'inputTokens' in usage or 'totalTokens' in usage
                            ):
                                usages.append(usage)
                            meta = node.get('_meta')
                            if isinstance(meta, dict):
                                tt = meta.get('totalTokens')
                                if isinstance(tt, (int, float)):
                                    max_ctx = max(max_ctx, int(tt))
                            for v in node.values():
                                walk(v)
                        elif isinstance(node, list):
                            for item in node:
                                walk(item)

                    walk(obj)
        except Exception as e:
            print(f'Grok parse error {updates_path}: {e}')
        return max_ctx, usages

    def _sum_usage(self, usages):
        tin = sum(int(u.get('inputTokens') or 0) for u in usages)
        tout = sum(int(u.get('outputTokens') or 0) for u in usages)
        ticks = sum(int(u.get('costUsdTicks') or 0) for u in usages)
        cached = sum(int(u.get('cachedReadTokens') or 0) for u in usages)
        return {
            'input_tokens': tin,
            'output_tokens': tout,
            'total_tokens': tin + tout,
            'cached_tokens': cached,
            'cost_usd': ticks / self.COST_TICKS_PER_USD,
            'turns': len(usages),
        }

    def _collect(self):
        if not os.path.isdir(self.sessions_root):
            return None

        now = time.time()
        week_ago = now - self.WEEKLY_SECONDS
        active_ids = self._active_session_ids()

        best_active_ctx = 0
        best_active_mtime = 0
        session_usages = []
        weekly_usages = []
        any_data = False

        for root, _dirs, files in os.walk(self.sessions_root):
            if 'updates.jsonl' not in files:
                continue
            updates_path = os.path.join(root, 'updates.jsonl')
            try:
                mtime = os.path.getmtime(updates_path)
            except OSError:
                continue

            sess_id = os.path.basename(root)
            max_ctx, usages = self._parse_session_updates(updates_path)
            if max_ctx or usages:
                any_data = True

            in_week = mtime >= week_ago
            is_active = sess_id in active_ids

            if is_active:
                if mtime >= best_active_mtime:
                    best_active_mtime = mtime
                    best_active_ctx = max_ctx
                    session_usages = usages
            elif not active_ids and in_week:
                if mtime >= best_active_mtime:
                    best_active_mtime = mtime
                    best_active_ctx = max_ctx
                    session_usages = usages

            if in_week and usages:
                weekly_usages.extend(usages)

        if not any_data:
            return None

        sess = self._sum_usage(session_usages)
        week = self._sum_usage(weekly_usages)
        return {
            'context_tokens': best_active_ctx,
            'context_window': self._context_window(),
            'session_tokens': sess['total_tokens'],
            'session_input': sess['input_tokens'],
            'session_output': sess['output_tokens'],
            'session_cost_usd': sess['cost_usd'],
            'session_turns': sess['turns'],
            'weekly_tokens': week['total_tokens'],
            'weekly_cost_usd': week['cost_usd'],
            'weekly_turns': week['turns'],
        }

    def run(self):
        while self.is_running:
            try:
                running = self._grok_running()
                data = self._collect()
                if data:
                    self.session_status.emit(True, 'Grok Build', '')
                    self.usage_fetched.emit(data)
                    self.msleep(4 if running else 8)
                elif running:
                    self.session_status.emit(True, 'Grok Build', '')
                    self.msleep(5)
                else:
                    self.session_status.emit(False, '', '')
                    self.msleep(10)
            except Exception as e:
                print(f'GrokSyncWorker error: {e}')
                self.session_status.emit(False, '', '')
                self.msleep(5)


# ════════════════════════════════════════════════════════════════════════════
#  ПОЛОСА ПРОГРЕССА (ГРАДИЕНТНАЯ)
# ════════════════════════════════════════════════════════════════════════════
def _pnum(v):
    try:
        return float(str(v).replace('%', '').strip())
    except Exception:
        return 0.0


def step_hex(pct):
    """Шкала: 0-30 зелёный, 30-60 светло-синий, 60-80 фиолетовый, 80-95 едкий оранжевый,
    95-99 оранжево-красный (пульсирует), 100 ярко-красный."""
    p = _pnum(pct)
    if p >= 100:
        return "#FF1744"
    if p >= 95:
        return "#FF5722" if int(time.monotonic()) % 2 == 0 else "#FF9E80"
    if p >= 80:
        return "#FF9100"
    if p >= 60:
        return "#B36BFF"
    if p >= 30:
        return "#4FC3F7"
    return "#2ECC71"


def status_color(pct) -> QColor:
    return QColor(step_hex(pct))


ALERT_PCT = 80          # с этого процента — ярко-красный
ALERT_RED = "#FF1744"




def pct_color(pct, normal=None):
    return step_hex(pct)


def pulse_on_change(label, value):
    """Плавно мигает надписью 4 раза, когда процент изменился."""
    prev = getattr(label, '_pulse_last', None)
    if prev == value:
        return
    label._pulse_last = value
    if prev is None:
        return  # первый показ — без мигания
    try:
        eff = label.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(label)
            label.setGraphicsEffect(eff)
        anim = getattr(label, '_pulse_anim', None)
        if anim is None:
            anim = QPropertyAnimation(eff, b"opacity", label)
            anim.setDuration(750)
            anim.setKeyValueAt(0.0, 1.0)
            anim.setKeyValueAt(0.5, 0.15)
            anim.setKeyValueAt(1.0, 1.0)
            anim.setEasingCurve(QEasingCurve.Type.InOutSine)
            anim.setLoopCount(4)
            label._pulse_anim = anim
        anim.stop()
        anim.start()
    except Exception:
        pass


class TokenProgressBar(QWidget):
    def __init__(self, parent=None, start_color="#667eea", end_color="#764ba2"):
        super().__init__(parent)
        self.value = 0
        self.start_color = QColor(start_color)
        self.end_color = QColor(end_color)
        self.setFixedHeight(10)

    def set_value(self, val):
        self.value = max(0, min(100, val))
        c = status_color(self.value)
        self.start_color = c.lighter(140)
        self.end_color = c
        self.update()

    def set_colors(self, start_color, end_color):
        self.start_color = QColor(start_color)
        self.end_color = QColor(end_color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 25)))
        p.drawRoundedRect(self.rect(), 5, 5)
        
        if self.value > 0:
            fill_w = int(self.width() * (self.value / 100.0))
            if fill_w > 0:
                fill_rect = QRectF(0, 0, fill_w, self.height())
                g = QLinearGradient(0, 0, self.width(), 0)
                g.setColorAt(0.0, self.start_color)
                g.setColorAt(1.0, self.end_color)
                p.setBrush(QBrush(g))
                p.drawRoundedRect(fill_rect, 5, 5)
        p.end()


class IOBar(QWidget):
    """Две полоски: вход (контекст) — голубая, выход (ответы Claude) — оранжевая."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.inp = 0
        self.out = 0
        self.setFixedHeight(26)

    def set_values(self, inp, out):
        self.inp, self.out = int(inp or 0), int(out or 0)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        w = self.width()
        mx = max(self.inp, self.out, 1)
        for i, (name, val, col) in enumerate((("▼ вход", self.inp, "#29b6f6"), ("▲ выход", self.out, "#ff9800"))):
            y = i * 13
            p.setPen(QColor(col))
            p.drawText(0, y, 62, 12, int(Qt.AlignmentFlag.AlignVCenter), name)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 25)))
            bar_w = w - 62 - 58
            p.drawRoundedRect(QRectF(62, y + 2, bar_w, 8), 4, 4)
            p.setBrush(QBrush(QColor(col)))
            p.drawRoundedRect(QRectF(62, y + 2, max(3, bar_w * val / mx) if val else 0, 8), 4, 4)
            p.setPen(QColor("#ffffff"))
            txt = f"{val/1000:.1f}k" if val >= 1000 else str(val)
            p.drawText(w - 54, y, 54, 12, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), txt)
        p.end()


# ════════════════════════════════════════════════════════════════════════════
#  ДИАЛОГ НАСТРОЕК
# ════════════════════════════════════════════════════════════════════════════
class SettingsDialog(QDialog):
    settings_applied = pyqtSignal(dict)
    opacity_preview = pyqtSignal(float)

    def __init__(self, current_session_limit, current_weekly_limit, current_opacity, on_top, locked, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Настройки: {TARGET_CFG['title']}")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.setFixedSize(300, 260)
        self.setStyleSheet("""
            QDialog {
                background: #1c202d;
                color: white;
            }
            QLabel {
                color: #eaf4ff;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            QLineEdit {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 5px;
                color: white;
                padding: 4px;
                font-family: 'Courier New';
                font-size: 11px;
            }
            QCheckBox {
                color: #eaf4ff;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            QPushButton {
                background: rgba(102,126,234,0.3);
                border: 1px solid rgba(102,126,234,0.5);
                border-radius: 5px;
                color: white;
                padding: 6px 12px;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(102,126,234,0.5);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        if TARGET != "antigravity":
            layout.addWidget(QLabel(f"{TARGET_CFG['session_label']} (лимит токенов):"))
            self.inp_session = QLineEdit(str(current_session_limit))
            self.inp_session.setValidator(QIntValidator(1000, 100000000))
            layout.addWidget(self.inp_session)

            layout.addWidget(QLabel(f"{TARGET_CFG['weekly_label']} (лимит токенов):"))
            self.inp_weekly = QLineEdit(str(current_weekly_limit))
            self.inp_weekly.setValidator(QIntValidator(10000, 1000000000))
            layout.addWidget(self.inp_weekly)

        layout.addWidget(QLabel("Прозрачность:"))
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(20, 100)
        self.slider_opacity.setValue(int(current_opacity * 100))
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        layout.addWidget(self.slider_opacity)

        chk_lay = QHBoxLayout()
        self.chk_top = QCheckBox("Поверх окон")
        self.chk_top.setChecked(on_top)
        self.chk_lock = QCheckBox("Закрепить")
        self.chk_lock.setChecked(locked)
        chk_lay.addWidget(self.chk_top)
        chk_lay.addWidget(self.chk_lock)
        layout.addLayout(chk_lay)

        btn_lay = QHBoxLayout()
        btn_ok = QPushButton("Сохранить")
        btn_ok.clicked.connect(self._on_save)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_lay.addStretch()
        btn_lay.addWidget(btn_ok)
        btn_lay.addWidget(btn_cancel)
        layout.addLayout(btn_lay)

        self.initial_opacity = current_opacity

    def _on_opacity_changed(self, val):
        self.opacity_preview.emit(val / 100.0)

    def _on_save(self):
        try:
            sess_val = int(self.inp_session.text()) if hasattr(self, 'inp_session') else 0
            week_val = int(self.inp_weekly.text()) if hasattr(self, 'inp_weekly') else 0
        except ValueError:
            return
        
        settings = {
            "opacity": self.slider_opacity.value() / 100.0,
            "on_top": self.chk_top.isChecked(),
            "locked": self.chk_lock.isChecked()
        }
        if TARGET != "antigravity":
            settings["session_limit"] = sess_val
            settings["weekly_limit"] = week_val
        self.settings_applied.emit(settings)
        self.accept()

    def reject(self):
        self.opacity_preview.emit(self.initial_opacity)
        super().reject()


# ════════════════════════════════════════════════════════════════════════════
#  МИНИ-БЕЙДЖ (компактный вид у трея после "свернуть")
# ════════════════════════════════════════════════════════════════════════════
class MiniBadge(QWidget):
    """Маленький (~2x2см) значок рядом с треем: модель + 5ч% + недельный%. Клик = развернуть обратно."""
    def __init__(self, parent_widget):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.parent_widget = parent_widget
        self.setFixedSize(82, 134 if TARGET == "antigravity" else 112)
        self.setToolTip("Клик - развернуть виджет")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(1)

        title_text = TARGET_CFG["title"]
        for prefix in ("\u26a1 ", "\ud83e\ude90 ", "\ud83d\udcbb ", "\u2726 "):
            title_text = title_text.replace(prefix, "")
        title_text = title_text.replace(" Usage", "")

        hdr_lay = QHBoxLayout()
        hdr_lay.setContentsMargins(0, 0, 0, 0)
        hdr_lay.setSpacing(3)
        hdr_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_files = {
            "claude": "claude.png",
            "codex": "codex.png",
            "antigravity": "antigravity.png",
        }
        icon_file = icon_files.get(TARGET)
        if icon_file:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", icon_file)
            if os.path.exists(icon_path):
                lbl_icon = QLabel()
                pix = QPixmap(icon_path).scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                lbl_icon.setPixmap(pix)
                hdr_lay.addWidget(lbl_icon)

        self.lbl_title = QLabel(title_text)
        self.lbl_title.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self.lbl_title.setStyleSheet("color: #ffffff;")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr_lay.addWidget(self.lbl_title)

        lay.addLayout(hdr_lay)

        self.lbl_session = QLabel("5\u0447: --%")
        self.lbl_session.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_session.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_session)

        self.lbl_session_timer = QLabel("--:--")
        self.lbl_session_timer.setFont(QFont("Segoe UI", 7))
        self.lbl_session_timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_session_timer.setStyleSheet("color: #bbbbbb;")
        lay.addWidget(self.lbl_session_timer)

        self.lbl_weekly = QLabel("\u041d\u0435\u0434: --%")
        self.lbl_weekly.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_weekly.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_weekly)

        self.lbl_weekly_date = QLabel("--.-- --:--")
        self.lbl_weekly_date.setFont(QFont("Segoe UI", 7))
        self.lbl_weekly_date.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_weekly_date.setStyleSheet("color: #c4b5fd;")
        lay.addWidget(self.lbl_weekly_date)

        if TARGET == "antigravity":
            self.lbl_argon = QLabel("Arg: --%")
            self.lbl_argon.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self.lbl_argon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_argon.setStyleSheet("color: #ff9f43; font-weight: bold;")
            lay.addWidget(self.lbl_argon)

    def update_values(self, session_pct, weekly_pct, session_timer_text="", weekly_date_text="", argon_pct=None):
        self.lbl_session.setText(f"5\u0447: {session_pct}%")
        self.lbl_session.setStyleSheet(f"color: {pct_color(session_pct)}; font-weight: bold;")
        pulse_on_change(self.lbl_session, session_pct)
        self.lbl_weekly.setText(f"\u041d\u0435\u0434: {weekly_pct}%")
        self.lbl_weekly.setStyleSheet(f"color: {pct_color(weekly_pct)}; font-weight: bold;")
        pulse_on_change(self.lbl_weekly, weekly_pct)
        if session_timer_text:
            self.lbl_session_timer.setText(session_timer_text)
            _m = re.match(r'\s*(\d+)\D+(\d+)\D+(\d+)', session_timer_text)
            if _m:
                _r = int(_m.group(1)) * 3600 + int(_m.group(2)) * 60 + int(_m.group(3))
                self.lbl_session_timer.setStyleSheet(timer_style(_r, "font-weight: bold;") if _r < 3600 else "color: #bbbbbb;")
        if weekly_date_text:
            self.lbl_weekly_date.setText(weekly_date_text)
            try:
                _ts = getattr(self.parent_widget, 'api_weekly_reset_ts', 0) or 0
                recolor_label(self.lbl_weekly_date, week_date_color((_ts - time.time()) if _ts else None, weekly_pct))
            except Exception:
                pass
        if hasattr(self, "lbl_argon") and argon_pct is not None:
            self.lbl_argon.setText(f"Arg: {argon_pct}%")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(18, 20, 28, 235)))
        p.drawRoundedRect(self.rect(), 14, 14)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._win_start = self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, "_drag_start"):
            delta = event.globalPosition().toPoint() - self._drag_start
            if abs(delta.x()) > 4 or abs(delta.y()) > 4:
                self._dragged = True
            if self._dragged:
                self.move(self._win_start + delta)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if getattr(self, "_dragged", False):
                # \u0417\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u0435\u043c \u043f\u043e\u0437\u0438\u0446\u0438\u044e \u0431\u0435\u0439\u0434\u0436\u0430, \u0447\u0442\u043e\u0431\u044b \u043e\u043d\u0430 \u043d\u0435 \u0441\u0431\u0440\u0430\u0441\u044b\u0432\u0430\u043b\u0430\u0441\u044c \u043a \u0443\u0433\u043b\u0443 \u043a\u0430\u0436\u0434\u044b\u0439 \u0440\u0430\u0437
                try:
                    self.parent_widget.cfg["badge_x"] = self.x()
                    self.parent_widget.cfg["badge_y"] = self.y()
                    if hasattr(self.parent_widget, "_save_cfg"):
                        self.parent_widget._save_cfg()
                except Exception:
                    pass
            else:
                self.hide()
                self.parent_widget.manually_hidden = False
                self.parent_widget.cfg["manually_hidden"] = False
                if hasattr(self.parent_widget, "_save_cfg"):
                    self.parent_widget._save_cfg()
                self.parent_widget.showNormal()
                self.parent_widget.show()
                self.parent_widget.raise_()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        a1 = m.addAction("🔼  Развернуть виджет")
        a2 = m.addAction("🔽  Убрать совсем (вернётся при открытии окна)")
        r = m.exec(e.globalPos())
        if r == a2:
            self.hide()
            self.parent_widget.manually_hidden = True
            self.parent_widget.cfg["manually_hidden"] = True
            self.parent_widget._save_cfg()
        elif r == a1:
            self.hide()
            self.parent_widget.manually_hidden = False
            self.parent_widget.cfg["manually_hidden"] = False
            self.parent_widget._save_cfg()
            self.parent_widget.showNormal(); self.parent_widget.show(); self.parent_widget.raise_()

    def show_near_tray(self):
        cfg = getattr(self.parent_widget, "cfg", {}) or {}
        bx = cfg.get("badge_x")
        by = cfg.get("badge_y")
        screen = QApplication.primaryScreen().availableGeometry()
        if bx is not None and by is not None:
            x = max(screen.left(), min(int(bx), screen.right() - self.width()))
            y = max(screen.top(), min(int(by), screen.bottom() - self.height()))
        else:
            x = screen.right() - self.width() - 8
            y = screen.bottom() - self.height() - 8
        self.move(x, y)
        self.show()
        self.raise_()
# ════════════════════════════════════════════════════════════════════════════
#  ГЛАВНЫЙ ВИДЖЕТ UniversalBudgetWidget
# ════════════════════════════════════════════════════════════════════════════
class UniversalBudgetWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._load_cfg()
        self.last_clipboard_text = QApplication.clipboard().text()
        self.is_online = False
        self.online_source = ""
        self.online_org = ""
        
        self.api_session_percent = 0.0
        self.api_weekly_percent = 0.0
        self.api_session_reset_ts = 0.0
        self.api_weekly_reset_ts = 0.0

        # Флаги для предотвращения повторного срабатывания алармов
        self.session_alarm_triggered = False
        self.weekly_alarm_triggered = False

        # Переменные состояния компактного режима и отслеживания активности
        # (восстанавливаются из cfg, чтобы пережить перезапуск процесса
        # виджета watchdog'ом при сворачивании/восстановлении Claude Desktop)
        self.manually_hidden = bool(self.cfg.get("manually_hidden", False))
        self.was_target_active = False
        self.is_collapsed = bool(self.cfg.get("is_collapsed", False))
        self.collapsible_antigravity_widgets = []
        self.collapsible_standard_widgets = []

        self._build_ui()
        if self.is_collapsed:
            # Применяем восстановленное состояние "свёрнуто" сразу при старте,
            # а не только при следующем нажатии на кнопку сворачивания.
            self.btn_collapse.setText("⏶")
            self.apply_collapse_state()
        self._setup_tray()
        self._setup_timers()
        self._start_sync_thread()
        self._drag_pos = QPoint()

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        
        tray_menu = QMenu()
        act_toggle = tray_menu.addAction("Показать/Скрыть")
        act_toggle.triggered.connect(self.toggle_visibility)
        
        act_settings = tray_menu.addAction("⚙️ Настройки...")
        act_settings.triggered.connect(self._open_settings)
        
        tray_menu.addSeparator()
        
        act_exit = tray_menu.addAction("Выход")
        act_exit.triggered.connect(self.close)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip(f"{TARGET.capitalize()} — клик: показать/скрыть")
        self.tray_icon.activated.connect(
            lambda r: self.toggle_visibility() if r in (QSystemTrayIcon.ActivationReason.Trigger,
                                                        QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray_icon.show()
        # Автовозврат: спрятанный вручную виджет появляется (в мини-виде), когда окно программы снова выходит на передний план
        self._app_was_fg = _target_is_foreground(TARGET)
        self._restore_timer = QTimer(self)
        self._restore_timer.timeout.connect(self._watch_app_restore)
        self._restore_timer.start(1000)

    def _watch_app_restore(self):
        try:
            fg = _target_is_foreground(TARGET)
            if fg and not self._app_was_fg and self.manually_hidden and not self.isVisible():
                if not hasattr(self, "mini_badge"):
                    self.mini_badge = MiniBadge(self)
                if not self.mini_badge.isVisible():
                    self.mini_badge.update_values(
                        getattr(self, "_last_session_pct", 0),
                        getattr(self, "_last_weekly_pct", 0),
                        argon_pct=getattr(self, "_last_argon_pct", None),
                    )
                    self.mini_badge.show_near_tray()
                    self._update_ui_state()
            self._app_was_fg = fg
        except Exception as ex:
            log_debug(f"restore watch error: {ex}")

    def toggle_visibility(self):
        if self.isVisible():
            self.manually_hidden = True
            self.hide()
        else:
            self.manually_hidden = False
            self.showNormal()
            self.show()
            self.raise_()
        self.cfg["manually_hidden"] = self.manually_hidden
        self._save_cfg()

    def _load_cfg(self):
        self.cfg = {
            "pos_x": 1800,
            "pos_y": 120,
            "opacity": 0.85,
            "locked": False,
            "on_top": True,
            "session_used": 0,
            "weekly_used": 0,
            "session_limit": TARGET_CFG["session_limit"],
            "weekly_limit": TARGET_CFG["weekly_limit"],
            "session_start_time": 0.0,
            "weekly_reset_time": 0.0,
            "auto_clipboard": False,
            "manually_hidden": False,
            "is_collapsed": False
        }
        if TARGET == "antigravity":
            self.cfg.update({
                "gemini_weekly_percent": 100,
                "gemini_weekly_reset_ts": 0.0,
                "gemini_5h_percent": 100,
                "gemini_5h_reset_ts": 0.0,
                "gemini_argon_weekly_percent": 100,
                "gemini_argon_weekly_reset_ts": 0.0,
                "3p_weekly_percent": 100,
                "3p_weekly_reset_ts": 0.0,
                "3p_5h_percent": 100,
                "3p_5h_reset_ts": 0.0,
            })
        if TARGET == "grok":
            self.cfg.setdefault("session_cost_usd", 0.0)
            self.cfg.setdefault("weekly_cost_usd", 0.0)
            self.cfg.setdefault("session_spend_tokens", 0)
            self.cfg.setdefault("budget_alerts", False)
            # старый ложный «бюджет сессии 1M» больше не используем
            self.cfg.pop("session_spend_limit", None)
            # если остался старый тесный бюджет 5M — расширяем (подписка ~€30)
            if int(self.cfg.get("weekly_limit") or 0) <= 5_000_000:
                self.cfg["weekly_limit"] = 50_000_000
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.cfg.update(loaded)
            except Exception:
                pass
        
        now = time.time()
        if not self.cfg.get("session_start_time"):
            self.cfg["session_start_time"] = now
        if not self.cfg.get("weekly_reset_time"):
            self.cfg["weekly_reset_time"] = now + WEEKLY_DURATION
        self._save_cfg()

    def _save_cfg(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _build_ui(self):
        self._update_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.update_widget_size()

        scr = QApplication.primaryScreen().geometry()
        x = max(0, min(self.cfg.get("pos_x", scr.width() - WIDGET_W - 10), scr.width() - WIDGET_W))
        y = max(0, min(self.cfg.get("pos_y", 120), scr.height() - 390))
        self.move(x, y)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # 1. Шапка (Выпадающий список выбора режима + Индикатор Статуса)
        header_lay = QHBoxLayout()
        
        self.combo_target = QComboBox()
        self.combo_target.addItems(["🪐 Antigravity", "⚡ Claude", "💻 Codex", "✦ Grok"])
        target_indices = {"antigravity": 0, "claude": 1, "codex": 2, "grok": 3}
        self.combo_target.setCurrentIndex(target_indices.get(TARGET, 0))
        self.combo_target.setStyleSheet("""
            QComboBox {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: white;
                padding: 2px 25px 2px 10px;
                font-family: 'Segoe UI';
                font-size: 11px;
                font-weight: bold;
            }
            QComboBox:hover {
                background: rgba(255, 255, 255, 0.15);
            }
            QComboBox::drop-down {
                border: none;
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 15px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid white;
                margin-right: 8px;
                margin-top: 2px;
            }
            QComboBox QAbstractItemView {
                background: #1c202d;
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.12);
                selection-background-color: rgba(102, 126, 234, 0.3);
                selection-color: white;
            }
        """)
        self.combo_target.currentIndexChanged.connect(self._on_target_changed)
        
        self.lbl_status = QLabel("● Ручной")
        self.lbl_status.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.lbl_status.setStyleSheet("color: rgba(255,165,0,0.8);")
        self.lbl_status.setToolTip("Режим ручного подсчета")

        header_lay.addWidget(self.combo_target)
        header_lay.addStretch()
        header_lay.addWidget(self.lbl_status)

        # Кнопка компактного вида (сжать/развернуть)
        self.btn_collapse = QPushButton("⏷")
        self.btn_collapse.setFixedSize(22, 22)
        self.btn_collapse.setFont(QFont("Segoe UI", 9))
        self.btn_collapse.setToolTip("Компактный вид / Развернуть")
        self.btn_collapse.setStyleSheet("""
            QPushButton { background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2);
                          border-radius: 4px; color: white; }
            QPushButton:hover { background: rgba(255,255,255,0.2); }
        """)
        self.btn_collapse.clicked.connect(self.toggle_collapse)
        header_lay.addWidget(self.btn_collapse)

        # Кнопка сворачивания в трей (—)
        self.btn_to_tray = QPushButton("—")
        self.btn_to_tray.setFixedSize(22, 22)
        self.btn_to_tray.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.btn_to_tray.setToolTip("Свернуть в системный трей")
        self.btn_to_tray.setStyleSheet("""
            QPushButton { background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2);
                          border-radius: 4px; color: white; }
            QPushButton:hover { background: rgba(220,50,50,0.4); }
        """)
        self.btn_to_tray.clicked.connect(self.minimize_to_tray)
        header_lay.addWidget(self.btn_to_tray)

        root.addLayout(header_lay)

        self.stacked_widget = QStackedWidget()
        
        # --- Page 0: Antigravity layout ---
        page_antigravity = QWidget()
        lay_antigravity = QVBoxLayout(page_antigravity)
        lay_antigravity.setContentsMargins(0, 0, 0, 0)
        lay_antigravity.setSpacing(8)
        
        # ── Переключатель модели: Gemini / Claude & GPT ──────────────────────
        self._antigravity_model = "gemini"  # текущий выбор: "gemini" или "3p"
        model_switch_lay = QHBoxLayout()
        model_switch_lay.setSpacing(4)

        self.btn_model_gemini = QPushButton("🌀 Gemini")
        self.btn_model_gemini.setFixedHeight(24)
        self.btn_model_gemini.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_model_gemini.setCheckable(True)
        self.btn_model_gemini.setChecked(True)
        self.btn_model_gemini.clicked.connect(lambda: self._switch_antigravity_model("gemini"))

        self.btn_model_3p = QPushButton("⚡ Claude & GPT")
        self.btn_model_3p.setFixedHeight(24)
        self.btn_model_3p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_model_3p.setCheckable(True)
        self.btn_model_3p.setChecked(False)
        self.btn_model_3p.clicked.connect(lambda: self._switch_antigravity_model("3p"))

        _model_btn_style_active = """
            QPushButton {
                background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.35);
                border-radius: 5px; color: white; padding: 2px 8px;
            }
        """
        _model_btn_style_inactive = """
            QPushButton {
                background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12);
                border-radius: 5px; color: rgba(255,255,255,0.45); padding: 2px 8px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.12); }
        """
        self.btn_model_gemini.setStyleSheet(_model_btn_style_active)
        self.btn_model_3p.setStyleSheet(_model_btn_style_inactive)
        self._model_btn_style_active = _model_btn_style_active
        self._model_btn_style_inactive = _model_btn_style_inactive

        model_switch_lay.addWidget(self.btn_model_gemini)
        model_switch_lay.addWidget(self.btn_model_3p)
        model_switch_lay.addStretch()
        lay_antigravity.addLayout(model_switch_lay)

        # ── Секция Gemini ─────────────────────────────────────────────────────
        self.widget_gemini_section = QWidget()
        lay_gemini_sec = QVBoxLayout(self.widget_gemini_section)
        lay_gemini_sec.setContentsMargins(0, 0, 0, 0)
        lay_gemini_sec.setSpacing(6)

        self.lbl_gemini_title = QLabel("Gemini Models")
        self.lbl_gemini_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_gemini_title.setStyleSheet("color: rgba(255,255,255,0.9);")
        lay_gemini_sec.addWidget(self.lbl_gemini_title)
        self.collapsible_antigravity_widgets.append(self.lbl_gemini_title)

        # Gemini 5h (сверху)
        self.lay_gemini_5h_hdr = self._create_section_header("Five Hour Limit", "gemini5hTimer", "#ec008c")
        lay_gemini_sec.addLayout(self.lay_gemini_5h_hdr)
        self.bar_gemini_5h = TokenProgressBar(self, "#ec008c", "#fc6767")
        lay_gemini_sec.addWidget(self.bar_gemini_5h)
        self.lbl_gemini_5h_progress = QLabel("Использовано: --")
        self.lbl_gemini_5h_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_gemini_5h_progress.setMinimumHeight(20)
        self.lbl_gemini_5h_progress.setStyleSheet("color: #FFB300; background: rgba(0,0,0,0.55); padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 13px;")
        lay_gemini_sec.addWidget(self.lbl_gemini_5h_progress)

        # Контейнер для Gemini Weekly (снизу)
        self.widget_gemini_weekly = QWidget()
        lay_g_w = QVBoxLayout(self.widget_gemini_weekly)
        lay_g_w.setContentsMargins(0, 0, 0, 0)
        lay_g_w.setSpacing(4)
        lay_g_w.addLayout(self._create_section_header("Weekly Limit", "geminiWeeklyTimer", "#00c6ff"))
        self.bar_gemini_weekly = TokenProgressBar(self, "#00c6ff", "#0072ff")
        lay_g_w.addWidget(self.bar_gemini_weekly)
        self.lbl_gemini_weekly_progress = QLabel("Использовано: --")
        self.lbl_gemini_weekly_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_gemini_weekly_progress.setMinimumHeight(20)
        self.lbl_gemini_weekly_progress.setStyleSheet("color: #FFB300; background: rgba(0,0,0,0.55); padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 13px;")
        lay_g_w.addWidget(self.lbl_gemini_weekly_progress)
        lay_gemini_sec.addWidget(self.widget_gemini_weekly)
        self.collapsible_antigravity_widgets.append(self.widget_gemini_weekly)

        # Контейнер для Gemini 4 Argon Weekly
        self.widget_gemini_argon = QWidget()
        lay_g_a = QVBoxLayout(self.widget_gemini_argon)
        lay_g_a.setContentsMargins(0, 0, 0, 0)
        lay_g_a.setSpacing(4)
        lay_g_a.addLayout(self._create_section_header("Gemini 4 Argon (Weekly)", "geminiArgonTimer", "#f5af19"))
        self.bar_gemini_argon = TokenProgressBar(self, "#f5af19", "#f12711")
        lay_g_a.addWidget(self.bar_gemini_argon)
        self.lbl_gemini_argon_progress = QLabel("Использовано: --")
        self.lbl_gemini_argon_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_gemini_argon_progress.setMinimumHeight(20)
        self.lbl_gemini_argon_progress.setStyleSheet("color: #FFB300; background: rgba(0,0,0,0.55); padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 13px;")
        lay_g_a.addWidget(self.lbl_gemini_argon_progress)
        lay_gemini_sec.addWidget(self.widget_gemini_argon)

        lay_antigravity.addWidget(self.widget_gemini_section)

        # ── Секция Claude & GPT ───────────────────────────────────────────────
        self.widget_3p_section = QWidget()
        lay_3p_sec = QVBoxLayout(self.widget_3p_section)
        lay_3p_sec.setContentsMargins(0, 0, 0, 0)
        lay_3p_sec.setSpacing(6)
        self.widget_3p_section.setVisible(False)  # скрыта по умолчанию

        self.lbl_claude_title = QLabel("Claude and GPT models")
        self.lbl_claude_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_claude_title.setStyleSheet("color: rgba(255,255,255,0.9);")
        lay_3p_sec.addWidget(self.lbl_claude_title)
        self.collapsible_antigravity_widgets.append(self.lbl_claude_title)

        # 3P 5h (сверху)
        self.lay_3p_5h_hdr = self._create_section_header("Five Hour Limit", "3p5hTimer", "#11998e")
        lay_3p_sec.addLayout(self.lay_3p_5h_hdr)
        self.bar_3p_5h = TokenProgressBar(self, "#11998e", "#38ef7d")
        lay_3p_sec.addWidget(self.bar_3p_5h)
        self.lbl_3p_5h_progress = QLabel("Использовано: --")
        self.lbl_3p_5h_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_3p_5h_progress.setMinimumHeight(20)
        self.lbl_3p_5h_progress.setStyleSheet("color: #FFB300; background: rgba(0,0,0,0.55); padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 13px;")
        lay_3p_sec.addWidget(self.lbl_3p_5h_progress)

        # Контейнер для 3P Weekly (снизу)
        self.widget_3p_weekly = QWidget()
        lay_3p_w = QVBoxLayout(self.widget_3p_weekly)
        lay_3p_w.setContentsMargins(0, 0, 0, 0)
        lay_3p_w.setSpacing(4)
        lay_3p_w.addLayout(self._create_section_header("Weekly Limit", "3pWeeklyTimer", "#f12711"))
        self.bar_3p_weekly = TokenProgressBar(self, "#f12711", "#f5af19")
        lay_3p_w.addWidget(self.bar_3p_weekly)
        self.lbl_3p_weekly_progress = QLabel("Использовано: --")
        self.lbl_3p_weekly_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_3p_weekly_progress.setMinimumHeight(20)
        self.lbl_3p_weekly_progress.setStyleSheet("color: #FFB300; background: rgba(0,0,0,0.55); padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 13px;")
        lay_3p_w.addWidget(self.lbl_3p_weekly_progress)
        lay_3p_sec.addWidget(self.widget_3p_weekly)
        self.collapsible_antigravity_widgets.append(self.widget_3p_weekly)

        lay_antigravity.addWidget(self.widget_3p_section)

        sep_anti_2 = self._sep()
        lay_antigravity.addWidget(sep_anti_2)
        self.collapsible_antigravity_widgets.append(sep_anti_2)

        self.lbl_chat_context_title = QLabel("Current Chat Context")
        self.lbl_chat_context_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_chat_context_title.setStyleSheet("color: rgba(255,255,255,0.9);")
        lay_antigravity.addWidget(self.lbl_chat_context_title)
        self.collapsible_antigravity_widgets.append(self.lbl_chat_context_title)

        # Chat Context
        self.lay_chat_context_hdr = self._create_section_header("Active Conversation", "chatContextTimer", "#a8ff78")
        lay_antigravity.addLayout(self.lay_chat_context_hdr)
        self.bar_chat_context = TokenProgressBar(self, "#a8ff78", "#78ffd6")
        lay_antigravity.addWidget(self.bar_chat_context)
        self.lbl_chat_context_progress = QLabel("Использовано: --")
        self.lbl_chat_context_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_chat_context_progress.setStyleSheet("color: #ffffff; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px;")
        lay_antigravity.addWidget(self.lbl_chat_context_progress)

        sep_anti_3 = self._sep()
        lay_antigravity.addWidget(sep_anti_3)
        self.collapsible_antigravity_widgets.append(sep_anti_3)

        self.stacked_widget.addWidget(page_antigravity)

        # --- Page 1: Claude/Codex layout ---
        page_standard = QWidget()
        lay_standard = QVBoxLayout(page_standard)
        lay_standard.setContentsMargins(0, 0, 0, 0)
        lay_standard.setSpacing(8)

        self.lbl_session_header = QLabel(TARGET_CFG["session_label"])
        self.lbl_session_header.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_session_header.setStyleSheet("color: rgba(255,255,255,0.9);")
        
        sess_timer = QLabel("--")
        sess_timer.setObjectName("sessionTimer")
        sess_timer.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        sess_timer.setStyleSheet("color: #FFB300; background: rgba(0,0,0,0.55); padding: 2px 5px; border-radius: 4px; font-weight: bold; font-size: 12px;")
        
        sess_hdr_lay = QHBoxLayout()
        sess_hdr_lay.addWidget(self.lbl_session_header)
        sess_hdr_lay.addStretch()
        sess_hdr_lay.addWidget(sess_timer)
        lay_standard.addLayout(sess_hdr_lay)

        self.session_bar = TokenProgressBar(self, TARGET_CFG["session_bar_colors"][0], TARGET_CFG["session_bar_colors"][1])
        lay_standard.addWidget(self.session_bar)
        
        # Контейнер подробной статистики сессии (скрывается при сжатии)
        self.widget_sess_details = QWidget()
        sess_det_lay = QVBoxLayout(self.widget_sess_details)
        sess_det_lay.setContentsMargins(0, 0, 0, 0)
        sess_det_lay.setSpacing(2)
        self.lbl_session_progress = QLabel("0 / 0 tokens")
        self.lbl_session_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_session_progress.setStyleSheet("color: #ffffff; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px;")
        self.lbl_session_remaining = QLabel("Осталось: --")
        self.lbl_session_remaining.setFont(QFont("Segoe UI", 8))
        self.lbl_session_remaining.setStyleSheet("color: rgba(255,255,255,0.45);")
        sess_lbl_lay = QHBoxLayout()
        sess_lbl_lay.addWidget(self.lbl_session_progress)
        sess_lbl_lay.addStretch()
        sess_lbl_lay.addWidget(self.lbl_session_remaining)
        sess_det_lay.addLayout(sess_lbl_lay)
        self.io_bar = IOBar(self)
        self.io_bar.setToolTip('Вход — сколько токенов Claude прочитал в последнем запросе (весь контекст).\nВыход — сколько токенов Claude написал за весь чат.')
        self.io_bar.setVisible(TARGET == 'claude')
        sess_det_lay.addWidget(self.io_bar)
        lay_standard.addWidget(self.widget_sess_details)
        self.collapsible_standard_widgets.append(self.widget_sess_details)
        self.collapsible_standard_widgets.append(self.lbl_session_header)
        self.collapsible_standard_widgets.append(self.session_bar)

        self.sep_standard_1 = self._sep()
        lay_standard.addWidget(self.sep_standard_1)
        self.collapsible_standard_widgets.append(self.sep_standard_1)

        # ── Блок "Лимит сообщений (5ч)" — всегда виден (в свёрнутом тоже) ──
        self.lbl_weekly_header = QLabel(TARGET_CFG["weekly_label"])
        self.lbl_weekly_header.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_weekly_header.setStyleSheet("color: rgba(255,255,255,0.9);")
        
        week_timer = QLabel("--")
        week_timer.setObjectName("weeklyTimer")
        week_timer.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        week_timer.setStyleSheet("color: #FFA726; background: rgba(0,0,0,0.4); padding: 2px 5px; border-radius: 4px; font-weight: bold;")
        
        week_hdr_lay = QHBoxLayout()
        week_hdr_lay.addWidget(self.lbl_weekly_header)
        week_hdr_lay.addStretch()
        week_hdr_lay.addWidget(week_timer)
        lay_standard.addLayout(week_hdr_lay)

        self.weekly_bar = TokenProgressBar(self, TARGET_CFG["weekly_bar_colors"][0], TARGET_CFG["weekly_bar_colors"][1])
        lay_standard.addWidget(self.weekly_bar)
        
        self.lbl_weekly_progress = QLabel("0 / 0 tokens")
        self.lbl_weekly_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_weekly_progress.setStyleSheet("color: #ffffff; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px;")
        
        self.lbl_weekly_remaining = QLabel("Осталось: --")
        self.lbl_weekly_remaining.setFont(QFont("Segoe UI", 8))
        self.lbl_weekly_remaining.setStyleSheet("color: rgba(255,255,255,0.45);")
        
        week_lbl_lay = QHBoxLayout()
        week_lbl_lay.addWidget(self.lbl_weekly_progress)
        week_lbl_lay.addStretch()
        week_lbl_lay.addWidget(self.lbl_weekly_remaining)
        lay_standard.addLayout(week_lbl_lay)

        # ── Разделитель перед недельным блоком (скрывается при сжатии) ──
        self.sep_standard_2 = self._sep()
        lay_standard.addWidget(self.sep_standard_2)
        self.collapsible_standard_widgets.append(self.sep_standard_2)

        # ── Блок "Недельный лимит" — только в развёрнутом виде ──
        self.widget_real_weekly = QWidget()
        real_wk_lay = QVBoxLayout(self.widget_real_weekly)
        real_wk_lay.setContentsMargins(0, 0, 0, 0)
        real_wk_lay.setSpacing(4)

        # Заголовок: "Недельный лимит" + дата сброса (день недели)
        self.lbl_real_weekly_header = QLabel("Недельный лимит")
        self.lbl_real_weekly_header.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_real_weekly_header.setStyleSheet("color: rgba(255,255,255,0.9);")

        self.lbl_real_weekly_reset = QLabel("Сброс: --")
        self.lbl_real_weekly_reset.setObjectName("realWeeklyResetLabel")
        self.lbl_real_weekly_reset.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.lbl_real_weekly_reset.setStyleSheet("color: #a78bfa; background: rgba(0,0,0,0.55); padding: 2px 5px; border-radius: 4px;")

        real_wk_hdr_lay = QHBoxLayout()
        real_wk_hdr_lay.addWidget(self.lbl_real_weekly_header)
        real_wk_hdr_lay.addStretch()
        real_wk_hdr_lay.addWidget(self.lbl_real_weekly_reset)
        real_wk_lay.addLayout(real_wk_hdr_lay)

        # Бегунок недельного лимита
        self.real_weekly_bar = TokenProgressBar(self, "#a78bfa", "#7c3aed")
        real_wk_lay.addWidget(self.real_weekly_bar)

        # Прогресс + остаток
        self.lbl_real_weekly_progress = QLabel("0% использовано")
        self.lbl_real_weekly_progress.setFont(QFont("Segoe UI", 8))
        self.lbl_real_weekly_progress.setStyleSheet("color: #ffffff; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px;")

        self.lbl_real_weekly_remaining = QLabel("Осталось: --")
        self.lbl_real_weekly_remaining.setFont(QFont("Segoe UI", 8))
        self.lbl_real_weekly_remaining.setStyleSheet("color: rgba(255,255,255,0.45);")

        real_wk_lbl_lay = QHBoxLayout()
        real_wk_lbl_lay.addWidget(self.lbl_real_weekly_progress)
        real_wk_lbl_lay.addStretch()
        real_wk_lbl_lay.addWidget(self.lbl_real_weekly_remaining)
        real_wk_lay.addLayout(real_wk_lbl_lay)

        lay_standard.addWidget(self.widget_real_weekly)
        # \u041d\u0435\u0434\u0435\u043b\u044c\u043d\u044b\u0439 \u043b\u0438\u043c\u0438\u0442 \u043e\u0441\u0442\u0430\u0451\u0442\u0441\u044f \u0432\u0438\u0434\u0438\u043c \u0438 \u0432 \u0441\u0432\u0451\u0440\u043d\u0443\u0442\u043e\u043c \u0432\u0438\u0434\u0435

        self.sep_standard_3 = self._sep()
        lay_standard.addWidget(self.sep_standard_3)
        self.collapsible_standard_widgets.append(self.sep_standard_3)

        self.stacked_widget.addWidget(page_standard)
        self.stacked_widget.setCurrentIndex(0 if TARGET == "antigravity" else 1)
        root.addWidget(self.stacked_widget)

        # Блок интерактивных кнопок (для ручного режима)
        self.widget_manual_controls = QWidget()
        manual_lay = QVBoxLayout(self.widget_manual_controls)
        manual_lay.setContentsMargins(0, 0, 0, 0)
        manual_lay.setSpacing(6)

        # 4. Быстрые кнопки добавления
        quick_lay = QHBoxLayout()
        quick_lay.setSpacing(6)
        quick_lay.addWidget(self._styled_btn("+2.5k", lambda: self.add_tokens(2500)))
        quick_lay.addWidget(self._styled_btn("+5k", lambda: self.add_tokens(5000)))
        quick_lay.addWidget(self._styled_btn("+10k", lambda: self.add_tokens(10000)))
        quick_lay.addWidget(self._styled_btn("+25k", lambda: self.add_tokens(25000)))
        manual_lay.addLayout(quick_lay)

        # 5. Ручной ввод и Ручной Буфер
        input_lay = QHBoxLayout()
        input_lay.setSpacing(6)
        
        self.btn_paste = QPushButton("📋 +Буфер")
        self.btn_paste.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.btn_paste.setFixedHeight(28)
        self.btn_paste.setStyleSheet("""
            QPushButton {
                background: rgba(100, 180, 255, 0.12);
                border: 1px solid rgba(100, 180, 255, 0.3);
                border-radius: 6px;
                color: #8cc5ff;
                padding: 0 10px;
            }
            QPushButton:hover { background: rgba(100, 180, 255, 0.22); }
        """)
        self.btn_paste.clicked.connect(self._add_from_clipboard_manual)
        
        self.inp_custom = QLineEdit()
        self.inp_custom.setPlaceholderText("Токены")
        self.inp_custom.setValidator(QIntValidator(0, 10000000))
        self.inp_custom.setFixedHeight(28)
        self.inp_custom.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self.inp_custom.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                color: white;
                padding: 0 6px;
                text-align: center;
            }
            QLineEdit:focus { border-color: rgba(100,180,255,0.5); background: rgba(255,255,255,0.1); }
        """)
        self.inp_custom.returnPressed.connect(self._add_custom_tokens)
        
        self.btn_add_custom = QPushButton("+")
        self.btn_add_custom.setFixedSize(30, 28)
        self.btn_add_custom.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.btn_add_custom.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: white;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.18); }
        """)
        self.btn_add_custom.clicked.connect(self._add_custom_tokens)
        
        input_lay.addWidget(self.btn_paste)
        input_lay.addWidget(self.inp_custom)
        input_lay.addWidget(self.btn_add_custom)
        manual_lay.addLayout(input_lay)
        root.addWidget(self.widget_manual_controls)

        # 6. Статус-бар / Тост-нотификация
        self.lbl_toast = QLabel("")
        self.lbl_toast.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.lbl_toast.setStyleSheet("color: #2ecc71;")
        self.lbl_toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_toast.setFixedHeight(14)
        root.addWidget(self.lbl_toast)

    def _update_window_flags(self):
        flags = (Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.Tool)
        if self.cfg.get("on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _create_section_header(self, text, timer_id, timer_color):
        lay = QHBoxLayout()
        lbl = QLabel(text)
        lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        lbl.setStyleSheet("color: rgba(255,255,255,0.9);")
        
        timer = QLabel("--")
        timer.setObjectName(timer_id)
        timer.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        timer.setStyleSheet(f"color: {timer_color}; background: rgba(0,0,0,0.4); padding: 2px 5px; border-radius: 4px; font-weight: bold;")
        
        lay.addWidget(lbl)
        lay.addStretch()
        lay.addWidget(timer)
        return lay

    def _sep(self):
        s = QFrame()
        s.setFrameShape(QFrame.Shape.HLine)
        s.setFixedHeight(1)
        s.setStyleSheet("background:rgba(255,255,255,0.12); border:none;")
        return s

    def _styled_btn(self, text, slot):
        b = QPushButton(text)
        b.setFixedHeight(24)
        b.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        b.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 5px;
                color: #eaf4ff;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.15); border-color: rgba(255,255,255,0.22); }
        """)
        b.clicked.connect(slot)
        return b

    def update_widget_size(self):
        if self.is_collapsed:
            if TARGET == "antigravity":
                h = 265 if getattr(self, "_antigravity_model", "gemini") == "gemini" else 200
            else:
                h = 175  # свёрнутый: контекст + 5ч бегунок
        elif TARGET == "antigravity":
            # Одна секция (Gemini или Claude) + переключатель + Chat Context
            h = 455 if getattr(self, "_antigravity_model", "gemini") == "gemini" else 390
        else:
            # развёрнутый: сессия + 5ч + недельный лимит
            h = 330 if self.is_online else 390
            if TARGET == "claude":
                h += 30  # полоска вход/выход
        self.setFixedSize(WIDGET_W, h)

    def toggle_collapse(self):
        """Переключение компактного/полного режима виджета"""
        self.is_collapsed = not self.is_collapsed
        self.btn_collapse.setText("⏶" if self.is_collapsed else "⏷")
        self.apply_collapse_state()
        self.update_widget_size()
        self.cfg["is_collapsed"] = self.is_collapsed
        self._save_cfg()

    def minimize_to_tray(self):
        """\u0421\u0432\u043e\u0440\u0430\u0447\u0438\u0432\u0430\u043d\u0438\u0435 \u0432\u0438\u0434\u0436\u0435\u0442\u0430 \u0432 \u043c\u0438\u043d\u0438-\u0431\u0435\u0439\u0434\u0436 \u0443 \u0442\u0440\u0435\u044f"""
        self.manually_hidden = True
        self.cfg["manually_hidden"] = True
        self._save_cfg()
        self.hide()
        if not hasattr(self, "mini_badge"):
            self.mini_badge = MiniBadge(self)
        self.mini_badge.update_values(
            getattr(self, "_last_session_pct", 0),
            getattr(self, "_last_weekly_pct", 0),
            argon_pct=getattr(self, "_last_argon_pct", None),
        )
        self.mini_badge.show_near_tray()
        self._update_ui_state()

    def apply_collapse_state(self):
        """Применить состояние сжатия/развёртки к виджетам"""
        if TARGET == "antigravity":
            for w in self.collapsible_antigravity_widgets:
                w.setVisible(not self.is_collapsed)
        else:
            for w in self.collapsible_standard_widgets:
                w.setVisible(not self.is_collapsed)
            # В свёрнутом виде скрываем текстовые подписи под 5-часовым бегунком
            self.lbl_weekly_remaining.setVisible(not self.is_collapsed)
            if hasattr(self, "lbl_real_weekly_remaining"): self.lbl_real_weekly_remaining.setVisible(not self.is_collapsed)
            timer_sess = self.findChild(QLabel, "sessionTimer")
            if timer_sess: timer_sess.setVisible(not self.is_collapsed)
            self.lbl_weekly_progress.setVisible(not self.is_collapsed)

    def _on_target_changed(self, index):
        global TARGET, TARGET_CFG, CONFIG_FILE
        targets = ["antigravity", "claude", "codex", "grok"]
        new_target = targets[index]
        if new_target == TARGET:
            return
            
        # 1. Stop current sync thread
        if hasattr(self, "sync_worker") and self.sync_worker:
            self.sync_worker.stop()
            self.sync_worker.wait()
            self.sync_worker = None
            
        # 2. Change the global TARGET state
        TARGET = new_target
        TARGET_CFG = TARGETS_CONFIG[TARGET]
        CONFIG_FILE = os.path.join(BASE_DIR, TARGET_CFG["config_file"])
        
        # 3. Reload config
        self._load_cfg()
        
        # 4. Update window properties
        self.setWindowTitle(f"Настройки: {TARGET_CFG['title']}")
        
        # 5. Switch stacked widget and update widgets
        if TARGET == "antigravity":
            self.stacked_widget.setCurrentIndex(0)
        else:
            self.stacked_widget.setCurrentIndex(1)
            self.session_bar.set_colors(TARGET_CFG["session_bar_colors"][0], TARGET_CFG["session_bar_colors"][1])
            self.weekly_bar.set_colors(TARGET_CFG["weekly_bar_colors"][0], TARGET_CFG["weekly_bar_colors"][1])
            self.lbl_session_header.setText(TARGET_CFG["session_label"])
            self.lbl_weekly_header.setText(TARGET_CFG["weekly_label"])
            self.lbl_session_progress.setText("0 / 0 tokens")
            self.lbl_weekly_progress.setText("0 / 0 tokens")
            
        # 6. Reset status
        self.is_online = False
        if not TARGET_CFG["enable_online"]:
            self.lbl_status.setText("● Ручной")
            self.lbl_status.setStyleSheet("color: rgba(255,165,0,0.8);")
            self.lbl_status.setToolTip("Режим ручного подсчета токенов")
            if hasattr(self, "widget_manual_controls") and self.widget_manual_controls:
                self.widget_manual_controls.setVisible(True)
        else:
            self.lbl_status.setText("● Офлайн")
            self.lbl_status.setStyleSheet("color: #e74c3c;")
            if TARGET == "antigravity":
                self.lbl_status.setToolTip("Нет связи с Antigravity")
            elif TARGET == "codex":
                self.lbl_status.setToolTip("Нет связи с Codex")
            elif TARGET == "grok":
                self.lbl_status.setToolTip("Нет данных Grok Build.\nЗапустите grok или дождитесь записи сессии в ~/.grok/sessions")
            else:
                self.lbl_status.setToolTip("Нет связи с API Claude")
            if hasattr(self, "widget_manual_controls") and self.widget_manual_controls:
                self.widget_manual_controls.setVisible(True)
                
        # 7. Start the new sync thread
        self._start_sync_thread()
        
        # 8. Force UI refresh
        self._update_ui_state()
        
        # 9. Resize window to correct size
        self.update_widget_size()

    def _switch_antigravity_model(self, model: str):
        """Переключает отображение секции Gemini или Claude & GPT на странице Antigravity."""
        self._antigravity_model = model
        is_gemini = (model == "gemini")

        # Показываем нужную секцию, скрываем другую
        if hasattr(self, "widget_gemini_section"):
            self.widget_gemini_section.setVisible(is_gemini)
        if hasattr(self, "widget_3p_section"):
            self.widget_3p_section.setVisible(not is_gemini)

        # Обновляем стили кнопок (активная / неактивная)
        if hasattr(self, "btn_model_gemini"):
            self.btn_model_gemini.setChecked(is_gemini)
            self.btn_model_gemini.setStyleSheet(
                self._model_btn_style_active if is_gemini else self._model_btn_style_inactive
            )
        if hasattr(self, "btn_model_3p"):
            self.btn_model_3p.setChecked(not is_gemini)
            self.btn_model_3p.setStyleSheet(
                self._model_btn_style_inactive if is_gemini else self._model_btn_style_active
            )

        # Подстраиваем высоту виджета
        self.update_widget_size()

    def _start_sync_thread(self):
        if not TARGET_CFG["enable_online"]:
            self.is_online = False
            self.lbl_status.setText("● Ручной")
            self.lbl_status.setStyleSheet("color: rgba(255,165,0,0.8);")
            self.lbl_status.setToolTip("Режим ручного подсчета токенов")
            if hasattr(self, "widget_manual_controls") and self.widget_manual_controls:
                self.widget_manual_controls.setVisible(True)
            self.update_widget_size()
            return

        if TARGET == "grok":
            self.sync_worker = GrokSyncWorker()
        elif TARGET in ("antigravity", "codex"):
            self.sync_worker = AntigravitySyncWorker()
        else:
            self.sync_worker = ClaudeSyncWorker()
            self.sync_worker.context_path = self.cfg.get('cowork_context_path', '')
            self.sync_worker.context_auto = self.cfg.get('cowork_context_auto', True)
            self.sync_worker.context_updated.connect(self._on_cowork_context)
            self.sync_worker.sessions_updated.connect(self._on_sessions_updated)
            self.sync_worker.diag_report.connect(self._on_diag_report)
            self.sync_worker.error_status.connect(self._on_error_status)
            self.available_sessions = []
        self.sync_worker.session_status.connect(self._on_session_status_changed)
        self.sync_worker.usage_fetched.connect(self._on_usage_fetched)
        self.sync_worker.start()


    def _on_cowork_context(self, data):
        self.cowork_context = data
        self._update_ui_state()

    def _select_cowork_task(self, path, title):
        self.cfg['cowork_context_path'] = path
        self.cfg['cowork_context_title'] = title
        self.cfg['cowork_context_auto'] = path is None
        self.sync_worker.context_auto = path is None
        self.sync_worker.context_path = path or ''
        self.sync_worker._last_context_check = 0
        self.cowork_context = {}
        self._save_cfg()
        self._update_ui_state()

    def _on_session_status_changed(self, is_valid, source_name, org_id):
        if TARGET == 'claude' and not is_valid:
            self.lbl_status.setText('● Нет связи')
            self.lbl_status.setToolTip('Восстанавливается подключение к Claude. Последние данные могут быть устаревшими.')
        if is_valid != self.is_online:
            self.is_online = is_valid
            if is_valid:
                self.online_source = source_name
                self.online_org = org_id
                self.lbl_status.setText("● Онлайн")
                self.lbl_status.setStyleSheet("color: #2ecc71;")
                if org_id:
                    self.lbl_status.setToolTip(f"Синхронизация с {source_name}\nОрганизация: {org_id}")
                else:
                    self.lbl_status.setToolTip(f"Синхронизация с {source_name}")
                if hasattr(self, "widget_manual_controls") and self.widget_manual_controls:
                    self.widget_manual_controls.setVisible(False)
                self.update_widget_size()
                self.show_toast(f"Подключено к {source_name}!")
                # Показываем виджет на экране, только если он не свёрнут
                # пользователем в мини-бейдж (иначе при подключении к API
                # поверх мини-бейджа всплывает ещё и полная карточка)
                if not self.manually_hidden:
                    self.showNormal()
                    self.show()
                    self.raise_()
                    self.activateWindow()
            else:
                if TARGET in ("antigravity", "codex"):
                    ide_name = "Antigravity" if TARGET == "antigravity" else "Codex"
                    self.lbl_status.setText("● Офлайн")
                    self.lbl_status.setStyleSheet("color: #e74c3c;")
                    self.lbl_status.setToolTip(f"Ожидание запуска {ide_name} IDE...")
                    if hasattr(self, "widget_manual_controls") and self.widget_manual_controls:
                        self.widget_manual_controls.setVisible(False)
                    self.update_widget_size()
                    self.show_toast(f"{ide_name} не запущен")
                    # Скрываем виджет с экрана
                    self.hide()
                else:
                    self.lbl_status.setText("● Нет связи" if TARGET == 'claude' else "● Ручной")
                    self.lbl_status.setStyleSheet("color: rgba(255,165,0,0.8);")
                    if TARGET == "grok":
                        self.lbl_status.setToolTip(
                            "Нет свежих данных Grok.\n"
                            "Запустите Grok Build — виджет читает токены и $ из ~/.grok/sessions"
                        )
                        toast_msg = "Ожидание сессии Grok Build..."
                    else:
                        self.lbl_status.setToolTip(
                            "Сессия claude.ai не найдена в браузере.\n"
                            "Войдите на claude.ai через Firefox или Chrome на этом компьютере - "
                            "виджет подключится и начнёт показывать реальные лимиты автоматически."
                        )
                        toast_msg = "Войдите на claude.ai в Firefox/Chrome для синхронизации"
                    if hasattr(self, "widget_manual_controls") and self.widget_manual_controls:
                        self.widget_manual_controls.setVisible(True)
                    self.update_widget_size()
                    self.show_toast(toast_msg)
            self._save_cfg()

    def _on_sessions_updated(self, sessions_list):
        self.available_sessions = sessions_list
        # Вход выполнен в открытом браузере → прячем его сам
        try:
            if os.path.exists(self._LOGIN_FLAG):
                prof = open(self._LOGIN_FLAG, encoding='utf-8').read().strip()
                pr = next((x for x in load_cdp_profiles() if x['profile'] == prof), None)
                em = pr and next((x.get('email') for x in sessions_list if x.get('source_name') == f"Chrome CDP:{pr['port']}"), None)
                if em:
                    self._widget_chrome_hide()
                    self.show_toast(f"Вход выполнен: {em}")
        except Exception:
            pass

    def _add_cdp_account(self):
        lst = load_cdp_profiles()
        port = max([x['port'] for x in lst] + [9230]) + 1
        while port in (9223, 9224, 9225, 9226):
            port += 1
        prof = f"ClaudeWidget{len(lst) + 1}"
        while any(x['profile'] == prof for x in lst):
            prof += 'x'
        lst.append({'port': port, 'profile': prof})
        save_cdp_profiles(lst)
        try:
            open(self._LOGIN_FLAG, 'w', encoding='utf-8').write(prof)
        except Exception:
            pass
        launch_cdp_chrome(port, prof, visible=True)
        self.show_toast("Новый аккаунт: войдите в открывшемся окне")

    def refresh_now(self):
        if getattr(self, 'sync_worker', None) and TARGET == 'claude':
            self.sync_worker.force_refresh = True
            self.show_toast('Ищу аккаунты и обновляю лимиты...')

    def _on_diag_report(self, text):
        box = QMessageBox(self)
        box.setWindowTitle('Обновление виджета Claude')
        box.setText(text)
        box.setWindowFlags(box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        box.show()

    def _on_error_status(self, err):
        badge = getattr(self, 'mini_badge', None)
        if badge is not None:
            if not hasattr(badge, '_title0'):
                badge._title0 = badge.lbl_title.text()
            approx = err.startswith('~')
            badge.lbl_title.setText(badge._title0 + (' ≈' if approx else ''))
            badge.lbl_title.setStyleSheet('color: #FFB300;' if approx else 'color: #ffffff;')
            badge.setToolTip((f'≈ Цифры из Claude Desktop на {err[1:]} — отстают до 15 мин.\n'
                              'Для точных: ПКМ по виджету → «Аккаунты: войти / добавить».\n') if approx else '' + 'Клик - развернуть виджет')
        if err.startswith('~'):
            self.lbl_status.setText('● ≈ ' + err[1:])
            self.lbl_status.setStyleSheet('color: #FFB300;')
            self.lbl_status.setToolTip(f'Цифры из Claude Desktop на {err[1:]}: обновляются раз в 15 минут и могут отставать.\n'
                                       'Точные в реальном времени — после входа: ПКМ → «Аккаунты: войти / добавить».')
            return
        if err:
            self.lbl_status.setText('● Ошибка')
            self.lbl_status.setStyleSheet('color: #e74c3c;')
            self.lbl_status.setToolTip('Последнее обновление не удалось: ' + err + '\nПКМ → «Обновить сейчас» покажет подробности.')
        elif self.is_online:
            self.lbl_status.setText('● Онлайн')
            self.lbl_status.setStyleSheet('color: #2ecc71;')

    def select_session(self, sk):
        if hasattr(self, "sync_worker") and self.sync_worker and TARGET == "claude":
            self.sync_worker.select_session(sk)
            self.show_toast("Переключение аккаунта...")

    def _on_usage_fetched(self, d):
        try:
            if TARGET == "antigravity":
                self.cfg["chat_tokens"] = d.get("chat_tokens", 0)
                response = d.get("response", {})
                groups = response.get("groups", [])
                for g in groups:
                    buckets = g.get("buckets", [])
                    for b in buckets:
                        b_id = b.get("bucketId", "")
                        rem_val = b.get("remaining", {}).get("value", 1.0)
                        percent = int(float(rem_val) * 100)
                        reset_sec = int(b.get("resetTime", {}).get("seconds", 0))
                        
                        if b_id == "gemini-weekly":
                            self.cfg["gemini_weekly_percent"] = percent
                            self.cfg["gemini_weekly_reset_ts"] = float(reset_sec)
                        elif b_id == "gemini-5h":
                            self.cfg["gemini_5h_percent"] = percent
                            self.cfg["gemini_5h_reset_ts"] = float(reset_sec)
                        elif "argon" in b_id:
                            self.cfg["gemini_argon_weekly_percent"] = percent
                            self.cfg["gemini_argon_weekly_reset_ts"] = float(reset_sec)
                        elif b_id == "3p-weekly":
                            self.cfg["3p_weekly_percent"] = percent
                            self.cfg["3p_weekly_reset_ts"] = float(reset_sec)
                        elif b_id == "3p-5h":
                            self.cfg["3p_5h_percent"] = percent
                            self.cfg["3p_5h_reset_ts"] = float(reset_sec)
            elif TARGET == "grok":
                # Факт из ~/.grok/sessions — не rate-limit Claude/Codex.
                ctx = int(d.get("context_tokens") or 0)
                sess_tokens = int(d.get("session_tokens") or 0)
                week_tokens = int(d.get("weekly_tokens") or 0)
                sess_cost = float(d.get("session_cost_usd") or 0.0)
                week_cost = float(d.get("weekly_cost_usd") or 0.0)

                # Подставляем реальный context_window модели (обычно 500k у grok-4.5)
                cw = int(d.get('context_window') or self.cfg.get('session_limit') or 500000)
                if cw > 0:
                    self.cfg['session_limit'] = cw
                # Контекст — факт заполненности окна (не деньги)
                self.cfg['session_used'] = ctx
                self.cfg['weekly_used'] = week_tokens  # факт, не cap по бюджету
                self.cfg["session_cost_usd"] = sess_cost
                self.cfg["weekly_cost_usd"] = week_cost
                self.cfg["session_spend_tokens"] = sess_tokens

                # % только относительно ВАШЕГО мягкого недельного бюджета (не «сгорел у xAI»)
                budget = max(1, int(self.cfg.get("weekly_limit") or TARGET_CFG["weekly_limit"]))
                self.api_session_percent = min(100.0, 100.0 * sess_tokens / budget)
                self.api_weekly_percent = min(100.0, 100.0 * week_tokens / budget)
                # Дата сброса бюджета — из конфига виджета (7 дней)
                self.api_weekly_reset_ts = float(self.cfg.get("weekly_reset_time") or 0.0)
                self.api_session_reset_ts = 0.0
            elif TARGET == "codex":
                response = d.get("response", d) if isinstance(d, dict) else {}
                if "rate_limit" in response:
                    rate_limit = response.get("rate_limit", {}) or {}
                    primary_window = rate_limit.get("primary_window", {}) or {}
                    secondary_window = rate_limit.get("secondary_window", {}) or {}

                    # OpenAI не гарантирует, что primary_window = 5ч, а secondary_window = неделя.
                    # На части планов (напр. Plus) API отдаёт ОДНО окно с limit_window_seconds=604800
                    # (7 дней), а secondary_window пуст. Определяем смысл окна по его реальной
                    # длительности, а не по позиции - иначе 5ч-блок показывает недельный сброс.
                    WEEKLY_WINDOW_THRESHOLD = 5 * 3600 + 1800  # >5.5ч считаем "недельным" окном

                    windows = []
                    if primary_window:
                        windows.append(primary_window)
                    if secondary_window:
                        windows.append(secondary_window)

                    win_5h = None
                    win_week = None
                    for w in windows:
                        dur = float(w.get("limit_window_seconds", 0) or 0)
                        if dur and dur > WEEKLY_WINDOW_THRESHOLD:
                            if win_week is None:
                                win_week = w
                        else:
                            if win_5h is None:
                                win_5h = w

                    self.codex_has_5h_window = win_5h is not None
                    self.codex_has_weekly_window = win_week is not None

                    if win_5h:
                        self.api_session_percent = float(win_5h.get("used_percent", 0.0))
                        self.api_session_reset_ts = float(win_5h.get("reset_at", 0.0) or 0.0)
                    else:
                        self.api_session_percent = 0.0
                        self.api_session_reset_ts = 0.0

                    if win_week:
                        self.api_weekly_percent = float(win_week.get("used_percent", 0.0))
                        self.api_weekly_reset_ts = float(win_week.get("reset_at", 0.0) or 0.0)
                    elif win_5h:
                        self.api_weekly_percent = float(win_5h.get("used_percent", 0.0))
                        self.api_weekly_reset_ts = float(win_5h.get("reset_at", 0.0) or 0.0)
                    else:
                        self.api_weekly_percent = 0.0
                        self.api_weekly_reset_ts = 0.0
                else:
                    groups = response.get("groups", [])
                    for g in groups:
                        buckets = g.get("buckets", [])
                        for b in buckets:
                            b_id = b.get("bucketId", "")
                            rem_val = b.get("remaining", {}).get("value", 1.0)
                            percent = int(float(rem_val) * 100)
                            reset_sec = int(b.get("resetTime", {}).get("seconds", 0))
                            
                            if b_id == "3p-5h":
                                self.api_session_percent = float(100 - percent)
                                self.api_session_reset_ts = float(reset_sec)
                            elif b_id == "3p-weekly":
                                self.api_weekly_percent = float(100 - percent)
                                self.api_weekly_reset_ts = float(reset_sec)
                
                self.cfg["session_used"] = int(self.cfg["session_limit"] * (self.api_session_percent / 100.0))
                self.cfg["weekly_used"] = int(self.cfg["weekly_limit"] * (self.api_weekly_percent / 100.0))
            else:
                five_hour = d.get("five_hour") or {}
                self.api_session_percent = float(five_hour.get("utilization", 0.0))
                
                res_s_str = five_hour.get("resets_at", "")
                if res_s_str:
                    self.api_session_reset_ts = datetime.fromisoformat(res_s_str.replace("Z", "+00:00")).timestamp()
                
                seven_day = d.get("seven_day") or {}
                self.api_weekly_percent = float(seven_day.get("utilization", 0.0))
                
                res_w_str = seven_day.get("resets_at", "")
                if res_w_str:
                    self.api_weekly_reset_ts = datetime.fromisoformat(res_w_str.replace("Z", "+00:00")).timestamp()

                # Для Claude/Codex НЕ перезаписываем session_used из API —
                # первый бегунок («Текущая сессия») работает только через буфер обмена
                # self.cfg["session_used"] остаётся как есть
                self.cfg["weekly_used"] = int(self.cfg["weekly_limit"] * (self.api_weekly_percent / 100.0))
                
            self._save_cfg()
            self._update_ui_state()
        except Exception as e:
            print("Error parsing fetched usage:", e)

    def _setup_timers(self):
        self._update_ui_state()
        
        self.sec_timer = QTimer(self)
        self.sec_timer.timeout.connect(self._on_second_tick)
        self.sec_timer.start(1000)

        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self.toast_timer = QTimer(self)
        self.toast_timer.timeout.connect(self._clear_toast)

        # Keep widget + mini badge above other windows (Windows sometimes demotes topmost tool windows)
        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self._enforce_topmost)
        self.topmost_timer.start(2000)

    def _enforce_topmost(self):
        if not self.cfg.get("on_top", True):
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            HWND_TOPMOST = -1
            SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
            wins = [self, getattr(self, "mini_badge", None)]
            for w in wins:
                if w is not None and w.isVisible():
                    user32.SetWindowPos(ctypes.c_void_p(int(w.winId())), ctypes.c_void_p(HWND_TOPMOST),
                                        0, 0, 0, 0, ctypes.c_uint(SWP_FLAGS))
        except Exception:
            pass

    def _on_second_tick(self):
        now = time.time()
        
        if not self.is_online:
            elapsed_session = now - self.cfg["session_start_time"]
            if elapsed_session >= SESSION_DURATION:
                self.cfg["session_used"] = 0
                self.cfg["session_start_time"] = now
                self.show_toast("Сессия сброшена (прошло 3ч 3м)")
            
            if now >= self.cfg["weekly_reset_time"]:
                self.cfg["weekly_used"] = 0
                self.cfg["weekly_reset_time"] = now + WEEKLY_DURATION
                self.show_toast("Недельный лимит сброшен")
        
        self._update_ui_state()

        # Событийное управление видимостью на основе активности целевого приложения
        # (только при смене состояния: было активно -> стало неактивно и наоборот)
        is_active = is_target_app_active(TARGET)
        if is_active and not self.was_target_active:
            # Приложение только что стало активным -> показываем виджет
            if not self.manually_hidden:
                self.showNormal()
                self.show()
                self.raise_()
        # Сворачивание приложения виджет НЕ прячет (прячется только вручную)
        self.was_target_active = is_active

    def trigger_alarm(self, title, message):
        try:
            # Воспроизводим системный звук предупреждения
            winsound.MessageBeep(winsound.MB_ICONWARNING)
        except Exception:
            QApplication.beep()
            
        if self.tray_icon:
            self.tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Warning,
                5000
            )

    def _update_ui_state(self):
        now = time.time()
        
        if TARGET == "antigravity":
            g_w_pct = self.cfg.get("gemini_weekly_percent", 100)
            g_5h_pct = self.cfg.get("gemini_5h_percent", 100)
            g_a_pct = self.cfg.get("gemini_argon_weekly_percent", 100)
            t_w_pct = self.cfg.get("3p_weekly_percent", 100)
            t_5h_pct = self.cfg.get("3p_5h_percent", 100)
            
            g_w_ts = self.cfg.get("gemini_weekly_reset_ts", 0.0)
            g_5h_ts = self.cfg.get("gemini_5h_reset_ts", 0.0)
            g_a_ts = self.cfg.get("gemini_argon_weekly_reset_ts", 0.0)
            t_w_ts = self.cfg.get("3p_weekly_reset_ts", 0.0)
            t_5h_ts = self.cfg.get("3p_5h_reset_ts", 0.0)
            
            def fmt_timer(ts):
                if not ts or ts <= now:
                    return "--"
                rem = ts - now
                d = int(rem // (24 * 3600))
                h = int((rem % (24 * 3600)) // 3600)
                m = int((rem % 3600) // 60)
                s = int(rem % 60)
                if d > 0:
                    return f"{d}д {h}ч"
                if h > 0:
                    return f"{h}ч {m}м"
                return f"{m}м {s}с"
                
            for timer_id, ts in [
                ("geminiWeeklyTimer", g_w_ts),
                ("gemini5hTimer", g_5h_ts),
                ("geminiArgonTimer", g_a_ts),
                ("3pWeeklyTimer", t_w_ts),
                ("3p5hTimer", t_5h_ts)
            ]:
                timer_lbl = self.findChild(QLabel, timer_id)
                if timer_lbl:
                    timer_lbl.setText(fmt_timer(ts))
            
            if hasattr(self, "bar_gemini_weekly"):
                self.bar_gemini_weekly.set_value(100 - g_w_pct)
                self.lbl_gemini_weekly_progress.setText(f"Использовано: <b>{100 - g_w_pct}%</b> (осталось {g_w_pct}%)")
            if hasattr(self, "bar_gemini_5h"):
                self.bar_gemini_5h.set_value(100 - g_5h_pct)
                self.lbl_gemini_5h_progress.setText(f"Использовано: <b>{100 - g_5h_pct}%</b> (осталось {g_5h_pct}%)")
            if hasattr(self, "bar_gemini_argon"):
                self.bar_gemini_argon.set_value(100 - g_a_pct)
                self.lbl_gemini_argon_progress.setText(f"Использовано: <b>{100 - g_a_pct}%</b> (осталось {g_a_pct}%)")
            if hasattr(self, "bar_3p_weekly"):
                self.bar_3p_weekly.set_value(100 - t_w_pct)
                self.lbl_3p_weekly_progress.setText(f"Использовано: <b>{100 - t_w_pct}%</b> (осталось {t_w_pct}%)")
            if hasattr(self, "bar_3p_5h"):
                self.bar_3p_5h.set_value(100 - t_5h_pct)
                self.lbl_3p_5h_progress.setText(f"Использовано: <b>{100 - t_5h_pct}%</b> (осталось {t_5h_pct}%)")

            # Мини-бейдж (сворачивание в трей) - берём активную секцию (Gemini или Claude/GPT)
            badge_argon = max(0, 100 - g_a_pct)
            self._last_argon_pct = badge_argon
            if getattr(self, "widget_3p_section", None) and self.widget_3p_section.isVisible():
                badge_5h = max(0, 100 - t_5h_pct)
                badge_w = max(0, 100 - t_w_pct)
                b_5h_reset_ts = self.cfg.get("3p_5h_reset_ts", 0)
                b_week_reset_ts = self.cfg.get("3p_weekly_reset_ts", 0)
            else:
                badge_5h = max(0, 100 - g_5h_pct)
                badge_w = max(0, 100 - g_w_pct)
                b_5h_reset_ts = self.cfg.get("gemini_5h_reset_ts", 0)
                b_week_reset_ts = self.cfg.get("gemini_weekly_reset_ts", 0)
            self._last_session_pct = badge_5h
            self._last_weekly_pct = badge_w
            if hasattr(self, "mini_badge") and self.mini_badge.isVisible():
                if b_5h_reset_ts and b_5h_reset_ts > now:
                    b_rem = b_5h_reset_ts - now
                    b_h = int(b_rem // 3600); b_m = int((b_rem % 3600) // 60); b_s = int(b_rem % 60)
                    badge_timer_txt = f"{b_h}\u0447 {b_m:02d}\u043c {b_s:02d}\u0441"
                else:
                    badge_timer_txt = "--:--"
                badge_date_txt = datetime.fromtimestamp(b_week_reset_ts).strftime("%d.%m %H:%M") if b_week_reset_ts else "--.-- --:--"
                self.mini_badge.update_values(badge_5h, badge_w, badge_timer_txt, badge_date_txt, badge_argon)
                
            # Chat context updates
            chat_tokens = self.cfg.get("chat_tokens", 0)
            chat_limit = 2000000
            chat_pct = min(100, int((chat_tokens / chat_limit) * 100))
            
            timer_chat_lbl = self.findChild(QLabel, "chatContextTimer")
            if timer_chat_lbl:
                timer_chat_lbl.setText("2.0M max")
                
            if hasattr(self, "bar_chat_context"):
                self.bar_chat_context.set_value(chat_pct)
                self.lbl_chat_context_progress.setText(f"Использовано: <b>{chat_tokens:,}</b> / 2,000,000 токенов (<b>{chat_pct}%</b>)")
                
            # Alarms checking
            # Session alarms (5h limit): <= 25% remaining (>= 75% used)
            session_warning = (g_5h_pct <= 25 or t_5h_pct <= 25)
            if session_warning:
                if not self.session_alarm_triggered:
                    self.session_alarm_triggered = True
                    lbl = "Gemini 5h" if g_5h_pct <= 25 else "Claude & GPT 5h"
                    val = g_5h_pct if g_5h_pct <= 25 else t_5h_pct
                    self.trigger_alarm(
                        f"⚠️ {TARGET_CFG['title']}",
                        f"Остаток лимита сессии {lbl} менее 25%!\nОсталось: {val}%"
                    )
            else:
                if g_5h_pct > 30 and t_5h_pct > 30:
                    self.session_alarm_triggered = False
                    
            # Weekly alarms: <= 10% remaining (>= 90% used)
            weekly_warning = (g_w_pct <= 10 or t_w_pct <= 10)
            if weekly_warning:
                if not self.weekly_alarm_triggered:
                    self.weekly_alarm_triggered = True
                    lbl = "Gemini Weekly" if g_w_pct <= 10 else "Claude & GPT Weekly"
                    val = g_w_pct if g_w_pct <= 10 else t_w_pct
                    self.trigger_alarm(
                        f"⚠️ {TARGET_CFG['title']}",
                        f"Остаток недельного лимита {lbl} менее 10%!\nОсталось: {val}%"
                    )
            else:
                if g_w_pct > 15 and t_w_pct > 15:
                    self.weekly_alarm_triggered = False
                    
            if g_5h_pct <= 20 or t_5h_pct <= 20 or g_w_pct <= 20 or t_w_pct <= 20:
                self.setStyleSheet("UniversalBudgetWidget { background: rgba(52, 32, 32, 235); }")
            else:
                self.setStyleSheet("")
        else:
            def _fmt_tok(n):
                n = int(n or 0)
                if n >= 1_000_000:
                    return f"{n / 1_000_000:.2f}M"
                if n >= 10_000:
                    return f"{n / 1000:.0f}k"
                if n >= 1000:
                    return f"{n / 1000:.1f}k"
                return f"{n}"

            # ── Таймеры ──
            timer_sess_lbl = self.findChild(QLabel, "sessionTimer")
            if timer_sess_lbl:
                if TARGET == "grok":
                    timer_sess_lbl.setText("окно")
                else:
                    timer_sess_lbl.setText(f"{self.cfg['session_limit']//1000}k max")

            timer_week_lbl = self.findChild(QLabel, "weeklyTimer")
            if timer_week_lbl:
                if TARGET == "grok":
                    timer_week_lbl.setText("факт $")
                else:
                    if TARGET == "codex" and not getattr(self, "codex_has_5h_window", True):
                        timer_week_lbl.setText("нет 5ч-лимита")
                    else:
                        if self.is_online and self.api_session_reset_ts > 0:
                            rem_5h = max(0, self.api_session_reset_ts - now)
                        else:
                            rem_5h = max(0, SESSION_DURATION - (now - self.cfg["session_start_time"]))
                        h_5h = int(rem_5h // 3600)
                        m_5h = int((rem_5h % 3600) // 60)
                        s_5h = int(rem_5h % 60)
                        timer_week_lbl.setText(f"{h_5h}ч {m_5h:02d}м {s_5h:02d}с")
                        timer_week_lbl.setStyleSheet(timer_style(rem_5h))

            lim_s = max(1, int(self.cfg["session_limit"]))
            used_s = int(self.cfg["session_used"] or 0)
            percent_s = int(min(100, (used_s / lim_s) * 100))

            # ── 1) Контекст ──
            self.session_bar.set_value(percent_s)
            self.lbl_session_progress.setText(
                f"<b>{_fmt_tok(used_s)}</b> / {_fmt_tok(lim_s)} ({percent_s}%)"
            )
            self.lbl_session_progress.setStyleSheet(
                "color: #FFB300; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px; font-weight: bold; font-size: 13px;"
            )
            if TARGET == "grok":
                self.lbl_session_progress.setText(
                    f"<b>{used_s // 1000}k</b> / <b>{lim_s // 1000}k</b> ({percent_s}%)"
                )
                self.lbl_session_remaining.setText(
                    f"Контекст чата · не деньги · свободно ~{(max(0, lim_s - used_s)) // 1000}k из {lim_s // 1000}k"
                )
            else:
                rem_tokens_s = max(0, lim_s - used_s)
                resp_s = rem_tokens_s // 2500
                self.lbl_session_remaining.setText(f"Осталось: {rem_tokens_s:,} (≈{resp_s} отв.)")

            # ── 2) Сессионный расход (Grok = факт; Claude/Codex = 5ч API) ──
            if TARGET == 'codex':
                self.lbl_session_progress.setToolTip('Локальный счётчик виджета. 128k в настройках не подтверждает лимит модели Codex.')
                if used_s >= 100000:
                    bright = int(time.monotonic() / 2) % 2 == 0
                    color = '#ffb347' if bright else '#ce852b'
                    self.session_bar.set_colors(color, '#e89025')
                    self.lbl_session_progress.setStyleSheet(
                        f'color: {color}; background: rgba(0,0,0,0.55); padding: 1px 5px; '
                        'border-radius: 4px; font-weight: bold; font-size: 13px;')
                    if timer_sess_lbl:
                        timer_sess_lbl.setText('⚠ Новый чат?')
                        timer_sess_lbl.setToolTip('100 000 по локальному счётчику: рассмотрите новый чат после завершения этапа. '
                            'Это не подтверждённое заполнение контекста открытой задачи Codex.')
                elif timer_sess_lbl:
                    timer_sess_lbl.setToolTip('Лимит задан в настройках виджета, а не получен от модели.')
            if TARGET == 'claude':
                ctx = getattr(self, 'cowork_context', {})
                tokens, limit = ctx.get('tokens'), ctx.get('limit')
                self.session_bar.set_value(min(100, 100 * tokens / limit) if tokens is not None and limit else 0)
                self.lbl_session_progress.setText(
                    f'{tokens:,} / {limit:,} ({100 * tokens / limit:.1f}%)' if tokens is not None and limit
                    else (f'{tokens:,} токенов' if tokens is not None else ('☁ Облачный чат' if ctx.get('stale') else 'Контекст: нет данных')))
                _t = ctx.get('title') or self.cfg.get('cowork_context_title') or 'Чат не найден'
                _m = ctx.get('mtime')
                if ctx.get('stale'):
                    self.lbl_session_remaining.setText('контекст не виден')
                    self.lbl_session_progress.setToolTip('Обычный режим «Claude» в Desktop работает в облаке и не пишет журнал на компьютер.\n'
                        'Виджет видит контекст только чатов «</> Code» и Cowork.\n'
                        + (f'Последний локальный: {_t} · ' + time.strftime('%d.%m %H:%M', time.localtime(_m)) if _m else ''))
                else:
                    self.lbl_session_remaining.setText((_t[:26] + ('…' if len(_t) > 26 else '')) + (time.strftime(' · %d.%m %H:%M', time.localtime(_m)) if _m else ''))
                self.io_bar.set_values(tokens or 0, ctx.get('output', 0))
                self.lbl_session_progress.setToolTip('Вход последнего запроса, включая кэш. После сжатия число может уменьшиться.\n'
                    + str(ctx.get('model', '')) + '\n' + str(ctx.get('timestamp', '')))
                if timer_sess_lbl:
                    timer_sess_lbl.setText('Последний запрос')
                # Порог напоминания независим от реальной вместимости модели.
                if tokens is not None and tokens >= 200000:
                    bright = int(time.monotonic() / 2) % 2 == 0
                    color = '#ffb347' if bright else '#ce852b'
                    self.session_bar.set_colors(color, '#e89025')
                    self.lbl_session_progress.setStyleSheet(
                        f'color: {color}; background: rgba(0,0,0,0.55); padding: 1px 5px; '
                        'border-radius: 4px; font-weight: bold; font-size: 13px;')
                    if timer_sess_lbl:
                        timer_sess_lbl.setText('⚠ Новый чат?')
                        timer_sess_lbl.setToolTip('200 000 токенов: рассмотрите новый чат после завершения этапа. '
                            'Это напоминание, а не ограничение Claude. Сначала сохраните результаты и краткую передачу.')
                    self.lbl_session_progress.setToolTip(self.lbl_session_progress.toolTip()
                        + '\nБольшой контекст: сохраните результаты и рассмотрите новый чат после завершения этапа.')
                elif timer_sess_lbl:
                    timer_sess_lbl.setToolTip('Статистика последнего запроса выбранной задачи Cowork.')
            percent_w = int(self.api_session_percent) if self.is_online or TARGET == 'claude' else 0
            self.weekly_bar.set_value(min(100, percent_w))
            if TARGET == "grok":
                spend = int(self.cfg.get("session_spend_tokens") or 0)
                cost = float(self.cfg.get("session_cost_usd") or 0.0)
                budget = max(1, int(self.cfg.get("weekly_limit") or 1))
                alerts = bool(self.cfg.get("budget_alerts", False))
                self.lbl_weekly_progress.setText(
                    f"<b>{_fmt_tok(spend)}</b> tok · ≈<b>${cost:.2f}</b>"
                )
                self.lbl_weekly_remaining.setText(
                    f"Счётчик API (оценка) · не лимит подписки · {percent_w}% от ориентира {_fmt_tok(budget)}"
                )
                if hasattr(self, "lbl_weekly_header"):
                    self.lbl_weekly_header.setText("Потрачено в сессии (счётчик)")
                col = status_color(percent_w).name() if alerts else "#c4b5fd"
                self.lbl_weekly_progress.setStyleSheet(
                    f"color: {col}; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px; font-weight: bold;"
                )
            else:
                if TARGET == "codex" and not getattr(self, "codex_has_5h_window", True):
                    self.lbl_weekly_progress.setText("нет 5ч-лимита")
                    self.lbl_weekly_remaining.setText("В вашем плане только недельный лимит")
                    self.lbl_weekly_progress.setStyleSheet(
                        "color: #9aa0a6; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px; font-weight: bold; font-size: 12px;"
                    )
                else:
                    self.lbl_weekly_progress.setText(f"<b>{percent_w}%</b> использовано")
                    self.lbl_weekly_remaining.setText(f"Осталось: {100 - percent_w}%")
                    pulse_on_change(self.lbl_weekly_progress, percent_w)
                    self.lbl_weekly_progress.setStyleSheet(
                        f"color: {pct_color(percent_w)}; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px; font-weight: bold; font-size: 13px;"
                    )

            # ── 3) Неделя ──
            percent_rw = 0
            if hasattr(self, "real_weekly_bar"):
                DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                percent_rw = int(self.api_weekly_percent) if self.is_online or TARGET == 'claude' else 0
                self.real_weekly_bar.set_value(min(100, percent_rw))
                if TARGET == "grok":
                    wtok = int(self.cfg.get("weekly_used") or 0)
                    wcost = float(self.cfg.get("weekly_cost_usd") or 0.0)
                    budget = max(1, int(self.cfg.get("weekly_limit") or 1))
                    alerts = bool(self.cfg.get("budget_alerts", False))
                    self.lbl_real_weekly_progress.setText(
                        f"<b>{_fmt_tok(wtok)}</b> tok · ≈<b>${wcost:.2f}</b>"
                    )
                    left = max(0, budget - wtok)
                    self.lbl_real_weekly_remaining.setText(
                        f"Ориентир {_fmt_tok(budget)}/нед · ещё ~{_fmt_tok(left)} · подписка ≠ этот %"
                    )
                    if hasattr(self, "lbl_real_weekly_header"):
                        self.lbl_real_weekly_header.setText("Потрачено за неделю (счётчик)")
                    col = status_color(percent_rw).name() if alerts else "#c4b5fd"
                    self.lbl_real_weekly_progress.setStyleSheet(
                        f"color: {col}; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px; font-weight: bold;"
                    )
                else:
                    self.lbl_real_weekly_progress.setText(f"<b>{percent_rw}%</b> использовано")
                    self.lbl_real_weekly_remaining.setText(f"Осталось: {100 - percent_rw}%")
                    pulse_on_change(self.lbl_real_weekly_progress, percent_rw)
                    self.lbl_real_weekly_progress.setStyleSheet(
                        f"color: {pct_color(percent_rw)}; background: rgba(0,0,0,0.55); padding: 1px 5px; border-radius: 4px; font-weight: bold; font-size: 13px;"
                    )
                if self.api_weekly_reset_ts > 0:
                    reset_dt = datetime.fromtimestamp(self.api_weekly_reset_ts)
                    day_name = DAYS_RU[reset_dt.weekday()]
                    self.lbl_real_weekly_reset.setText(f"Сброс: {day_name} {reset_dt.strftime('%d.%m %H:%M')}")
                    recolor_label(self.lbl_real_weekly_reset, week_date_color(self.api_weekly_reset_ts - now, percent_rw))
                elif self.cfg.get("weekly_reset_time", 0) > 0:
                    reset_dt = datetime.fromtimestamp(self.cfg["weekly_reset_time"])
                    day_name = DAYS_RU[reset_dt.weekday()]
                else:
                    self.lbl_real_weekly_reset.setText("\u0421\u0431\u0440\u043e\u0441: --")

            self._last_session_pct = percent_w
            self._last_weekly_pct = percent_rw if hasattr(self, "real_weekly_bar") else 0
            if hasattr(self, "mini_badge") and self.mini_badge.isVisible():
                if self.api_session_reset_ts > 0:
                    b_rem = max(0, self.api_session_reset_ts - now)
                else:
                    b_rem = max(0, SESSION_DURATION - (now - self.cfg.get("session_start_time", now)))
                b_h = int(b_rem // 3600); b_m = int((b_rem % 3600) // 60); b_s = int(b_rem % 60)
                badge_timer_txt = f"{b_h}\u0447 {b_m:02d}\u043c {b_s:02d}\u0441"
                if self.api_weekly_reset_ts > 0:
                    b_rdt = datetime.fromtimestamp(self.api_weekly_reset_ts)
                elif self.cfg.get("weekly_reset_time", 0) > 0:
                    b_rdt = datetime.fromtimestamp(self.cfg["weekly_reset_time"])
                else:
                    b_rdt = None
                badge_date_txt = b_rdt.strftime("%d.%m %H:%M") if b_rdt else "--.-- --:--"
                self.mini_badge.update_values(self._last_session_pct, self._last_weekly_pct, badge_timer_txt, badge_date_txt)

            # \u2500\u2500 \u0410\u043b\u0430\u0440\u043c\u044b \u2500\u2500
            if TARGET == "grok":
                alerts = bool(self.cfg.get("budget_alerts", False))
                # Жёсткий локальный сигнал — только полное окно контекста (500k)
                if percent_s >= 90:
                    if not self.session_alarm_triggered:
                        self.session_alarm_triggered = True
                        self.trigger_alarm(
                            f"⚠️ {TARGET_CFG['title']}",
                            f"Контекст почти полный: {percent_s}% ({_fmt_tok(used_s)} / {_fmt_tok(lim_s)})"
                        )
                elif percent_s < 85:
                    self.session_alarm_triggered = False

                # $ / токены — только если явно включили budget_alerts
                if alerts and percent_rw >= 90:
                    if not self.weekly_alarm_triggered:
                        self.weekly_alarm_triggered = True
                        self.trigger_alarm(
                            f"⚠️ {TARGET_CFG['title']}",
                            f"Ваш ориентир недели почти выбран: {percent_rw}%\n"
                            f"≈${float(self.cfg.get('weekly_cost_usd') or 0):.2f} · "
                            f"{_fmt_tok(self.cfg.get('weekly_used') or 0)} tok"
                        )
                elif percent_rw < 85:
                    self.weekly_alarm_triggered = False

                if percent_s >= 92:
                    self.setStyleSheet("UniversalBudgetWidget { background: rgba(52, 32, 32, 235); }")
                else:
                    self.setStyleSheet("")
            else:
                if percent_s >= 75:
                    if not self.session_alarm_triggered:
                        self.session_alarm_triggered = True
                        self.trigger_alarm(
                            f"⚠️ {TARGET_CFG['title']}",
                            f"Остаток контекстного окна менее 25%!\nИспользовано: {percent_s}% ({used_s:,}/{lim_s:,})"
                        )
                else:
                    if percent_s < 70:
                        self.session_alarm_triggered = False

                if percent_w >= 90:
                    if not self.weekly_alarm_triggered:
                        self.weekly_alarm_triggered = True
                        self.trigger_alarm(
                            f"⚠️ {TARGET_CFG['title']}",
                            f"Остаток 5-часового лимита сообщений менее 10%!\nИспользовано: {percent_w}%"
                        )
                else:
                    if percent_w < 85:
                        self.weekly_alarm_triggered = False

                if percent_s >= 80 or percent_w >= 80:
                    self.setStyleSheet("UniversalBudgetWidget { background: rgba(52, 32, 32, 235); }")
                else:
                    self.setStyleSheet("")

    def add_tokens(self, val, source=""):
        # Для Antigravity в онлайн-режиме ввод токенов отключён (данные приходят по API)
        # Для Claude/Codex буфер обмена работает даже в онлайне, т.к. API не даёт размер конкретного чата
        if TARGET == "antigravity" and self.is_online:
            return
        if val <= 0:
            return
        self.cfg["session_used"] = min(self.cfg["session_limit"], self.cfg["session_used"] + val)
        self.cfg["weekly_used"] = min(self.cfg["weekly_limit"], self.cfg["weekly_used"] + val)
        self._save_cfg()
        self._update_ui_state()
        src_text = f" из {source}" if source else ""
        self.show_toast(f"+{val:,} токенов{src_text}!")

    def estimate_tokens(self, text):
        if not text:
            return 0
        cyrillic = 0
        other = 0
        for char in text:
            if '\u0400' <= char <= '\u04FF':
                cyrillic += 1
            elif char.isalnum() or char.isspace():
                other += 1
            else:
                other += 0.5
        est = int((cyrillic / 2.2) + (other / 4.0))
        return max(1, est)

    def _add_custom_tokens(self):
        val_str = self.inp_custom.text().strip()
        if val_str:
            val = int(val_str)
            self.add_tokens(val, "ввода")
            self.inp_custom.clear()

    def _on_clipboard_changed(self):
        if TARGET == "antigravity" and self.is_online:
            return
        if not self.cfg.get("auto_clipboard", False):
            return
        text = QApplication.clipboard().text()
        if text and text != self.last_clipboard_text:
            self.last_clipboard_text = text
            tokens = self.estimate_tokens(text)
            if tokens > 15:
                self.add_tokens(tokens, "буфера (авто)")

    def _add_from_clipboard_manual(self):
        if TARGET == "antigravity" and self.is_online:
            self.show_toast("В режиме авто-синхронизации ввод отключен!")
            return
        text = QApplication.clipboard().text()
        if not text:
            self.show_toast("Буфер обмена пуст!")
            return
        tokens = self.estimate_tokens(text)
        self.add_tokens(tokens, "буфера")

    # --- 2026-11-04: показать/скрыть фоновый Chrome виджета для входа ---
    _LOGIN_FLAG = os.path.join(os.environ.get("TEMP", "."), "claude_widget_login.flag")

    def _widget_chrome_windows(self, profile):
        import ctypes
        from ctypes import wintypes
        pids = set()
        try:
            import psutil
            needle = ("User Data\\" + profile).lower()
            for pr in psutil.process_iter(["name", "cmdline"]):
                if (pr.info.get("name") or "").lower() != "chrome.exe":
                    continue
                cl = " ".join(pr.info.get("cmdline") or []).lower()
                i = cl.find(needle)
                if i >= 0 and cl[i + len(needle):i + len(needle) + 1] in ('"', ' ', ''):
                    pids.add(pr.pid)
        except Exception:
            pass
        u = ctypes.windll.user32
        res = []
        PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, l):
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value in pids and u.GetWindowTextLengthW(h) > 0:
                buf = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(h, buf, 256)
                if buf.value.startswith("Chrome_WidgetWin"):
                    res.append(h)
            return True
        u.EnumWindows(PROC(cb), 0)
        return res, pids

    def _widget_chrome_show(self, profile):
        import ctypes
        try:
            io_ = open(self._LOGIN_FLAG, "w"); io_.write(profile); io_.close()
        except Exception:
            pass
        wins, pids = self._widget_chrome_windows(profile)
        if not pids:
            _pr = next((x for x in load_cdp_profiles() if x['profile'] == profile), None)
            if _pr:
                launch_cdp_chrome(_pr['port'], profile, visible=True)
            self.show_toast("Открываю браузер — войдите в claude.ai, окно спрячется само")
            return
        u = ctypes.windll.user32
        for h in wins:
            u.ShowWindow(h, 9)  # SW_RESTORE
            u.SetWindowPos(h, 0, 120, 80, 560, 800, 0x0040)  # SWP_SHOWWINDOW
            u.SetForegroundWindow(h)
        self.show_toast("Войдите в claude.ai — после входа окно спрячется само")

    def _widget_chrome_hide(self):
        import ctypes
        try:
            os.remove(self._LOGIN_FLAG)
        except Exception:
            pass
        u = ctypes.windll.user32
        for prof in [x['profile'] for x in load_cdp_profiles()]:
            wins, _ = self._widget_chrome_windows(prof)
            for h in wins:
                u.SetWindowPos(h, 0, -32000, -32000, 0, 0, 0x0001 | 0x0004)
                u.ShowWindow(h, 0)
        self.show_toast("Браузеры виджета скрыты")

    def show_toast(self, text):
        self.lbl_toast.setText(text)
        self.toast_timer.start(3000)

    def _clear_toast(self):
        self.lbl_toast.setText("")
        self.toast_timer.stop()

    # ── Рисование ────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        alpha = int(self.cfg.get("opacity", 0.85) * 255)
        p.setBrush(QBrush(QColor(32, 35, 47, alpha)))
        p.drawRoundedRect(self.rect(), 18, 18)

        g = QLinearGradient(0, 0, 0, self.height() * 0.5)
        g.setColorAt(0.0, QColor(255, 255, 255, min(40, alpha // 5)))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(self.rect(), 18, 18)

        pen = QPen(QColor(255, 255, 255, min(65, alpha // 4)))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 17.5, 17.5)
        p.end()

    # ── Перемещение ──────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.cfg.get("locked", False):
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and not self.cfg.get("locked", False):
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.cfg.get("locked", False):
            self.cfg["pos_x"] = self.x()
            self.cfg["pos_y"] = self.y()
            self._save_cfg()

    # ── Контекстное меню ─────────────────────────────────────────────────────
    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: rgba(28,32,45,240);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }
            QMenu::item { padding: 6px 20px; border-radius: 6px; }
            QMenu::item:selected { background: rgba(102,126,234,0.3); }
            QMenu::separator { height: 1px; background: rgba(255,255,255,0.1); margin: 4px 6px; }
        """)

        def act(text, slot, checkable=False, checked=False, enabled=True):
            a = QAction(text, self)
            if checkable:
                a.setCheckable(True)
                a.setChecked(checked)
            a.setEnabled(enabled)
            a.triggered.connect(slot)
            return a

        menu.addAction(act("🔽  Свернуть в трей (вернуть — клик по иконке)", self.toggle_visibility))
        menu.addSeparator()
        menu.addAction(act("⚙️  Настройки...", self._open_settings))
        if TARGET == 'claude':
            from cowork_context import list_tasks
            tasks_menu = menu.addMenu('Контекст: какой чат показывать')
            auto_ctx = QAction(('● ' if self.cfg.get('cowork_context_auto', True) else '') + '🔄 Авто — последний активный чат', self)
            auto_ctx.triggered.connect(lambda checked: self._select_cowork_task(None, ''))
            tasks_menu.addAction(auto_ctx)
            tasks_menu.addSeparator()
            for task in list_tasks():
                task_action = QAction(task['title'], self)
                task_action.setCheckable(True)
                task_action.setChecked(not self.cfg.get('cowork_context_auto', True) and task['path'] == self.cfg.get('cowork_context_path'))
                task_action.triggered.connect(lambda checked, p=task['path'], t=task['title']: self._select_cowork_task(p, t))
                tasks_menu.addAction(task_action)
        
        if TARGET == "claude":
            menu.addSeparator()
            menu.addAction(act("🔄  Обновить сейчас (найти аккаунты)", self.refresh_now))
        if TARGET == "claude" and hasattr(self, "available_sessions") and self.available_sessions:
            menu.addSeparator()
            acc_menu = menu.addMenu("👤  Выбрать аккаунт")
            auto_on = not (hasattr(self, "sync_worker") and self.sync_worker and self.sync_worker.selected_session_key)
            auto_item = QAction(("● " if auto_on else "") + "🔄 Авто — как в Claude Desktop", self)
            auto_item.triggered.connect(lambda checked: self.select_session(None))
            acc_menu.addAction(auto_item)
            acc_menu.addSeparator()
            for s in self.available_sessions:
                email = s.get('email') or 'Неизвестный'
                src = s.get('source_name') or 'Куки'
                is_active = False
                if hasattr(self, "sync_worker") and self.sync_worker:
                    is_active = (s['session_key'] == self.sync_worker.session_key)
                text = f"{email} ({src})"
                if is_active:
                    text = "● " + text
                action_item = QAction(text, self)
                action_item.triggered.connect(lambda checked, sk=s['session_key']: self.select_session(sk))
                acc_menu.addAction(action_item)
                
        menu.addSeparator()
        
        if TARGET == "claude":
            login_menu = menu.addMenu("🔑  Аккаунты: войти / добавить")
            _sess = getattr(self, 'available_sessions', []) or []
            for _i, _pr in enumerate(load_cdp_profiles(), 1):
                _em = next((x.get('email') for x in _sess if x.get('source_name') == f"Chrome CDP:{_pr['port']}"), None)
                _n = (f"✅ {_em}" if _em else f"⚠ Аккаунт {_i} — вход не выполнен") + "  (открыть браузер)"
                _a = QAction(_n, self)
                _a.triggered.connect(lambda checked, pr=_pr['profile']: self._widget_chrome_show(pr))
                login_menu.addAction(_a)
            login_menu.addSeparator()
            _add = QAction("➕  Добавить аккаунт", self)
            _add.triggered.connect(lambda checked: self._add_cdp_account())
            login_menu.addAction(_add)
            _h = QAction("🙈  Скрыть браузеры виджета", self)
            _h.triggered.connect(lambda checked: self._widget_chrome_hide())
            menu.addAction(_h)
            menu.addSeparator()
        menu.addAction(act("🔄  Сбросить сессию", self.reset_session, enabled=not self.is_online and TARGET != "antigravity"))
        menu.addAction(act("📅  Сбросить неделю", self.reset_weekly, enabled=not self.is_online and TARGET != "antigravity"))
        menu.addSeparator()
        
        menu.addAction(act("📌  Поверх всех окон", 
                           lambda v: self._toggle_cfg_bool("on_top", v),
                           checkable=True, checked=self.cfg.get("on_top", True)))
        menu.addAction(act("🔒  Закрепить положение", 
                           lambda v: self._toggle_cfg_bool("locked", v),
                           checkable=True, checked=self.cfg.get("locked", False)))
        menu.addAction(act("🚀  Автозапуск с Windows",
                           self._toggle_startup,
                           checkable=True, checked=self._startup_exists()))
        
        if not self.is_online:
            menu.addSeparator()
            menu.addAction(act("📋  Автоматический буфер",
                               lambda v: self._toggle_cfg_bool("auto_clipboard", v),
                               checkable=True, checked=self.cfg.get("auto_clipboard", False)))
                               
        menu.addSeparator()
        menu.addAction(act("❌  Закрыть виджет", self.close))
        # Меню и подменю всегда поверх; сам виджет на время меню почти прозрачный, чтобы не перекрывал
        for _m in [menu] + menu.findChildren(QMenu):
            _m.setWindowFlags(_m.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        _op = self.windowOpacity()
        self.setWindowOpacity(0.12)
        try:
            menu.exec(e.globalPos())
        finally:
            self.setWindowOpacity(_op)

    def _toggle_cfg_bool(self, key, value):
        self.cfg[key] = value
        self._save_cfg()
        if key == "on_top":
            pos = self.pos()
            self._update_window_flags()
            self.show()
            self.move(pos)
            self.raise_()

    def _open_settings(self):
        dlg = SettingsDialog(
            current_session_limit=self.cfg.get("session_limit", TARGET_CFG["session_limit"]),
            current_weekly_limit=self.cfg.get("weekly_limit", TARGET_CFG["weekly_limit"]),
            current_opacity=self.cfg.get("opacity", 0.85),
            on_top=self.cfg.get("on_top", True),
            locked=self.cfg.get("locked", False),
            parent=self
        )
        dlg.settings_applied.connect(self._apply_settings_dict)
        dlg.opacity_preview.connect(self._preview_opacity)
        
        gp = self.mapToGlobal(QPoint(0, 0))
        dlg.move(gp.x() + (self.width() - dlg.width()) // 2,
                 gp.y() + (self.height() - dlg.height()) // 2)
        dlg.exec()

    def _preview_opacity(self, val):
        self.cfg["opacity"] = val
        self.repaint()

    def _apply_settings_dict(self, d):
        self.cfg.update(d)
        self._save_cfg()
        
        pos = self.pos()
        self._update_window_flags()
        self.show()
        self.move(pos)
        self.raise_()
        self._update_ui_state()

    def reset_session(self):
        if QMessageBox.question(self, "Сброс", "Сбросить текущую сессию токенов?", 
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.cfg["session_used"] = 0
            self.cfg["session_start_time"] = time.time()
            self._save_cfg()
            self._update_ui_state()
            self.show_toast("Сессия сброшена")

    def reset_weekly(self):
        if QMessageBox.question(self, "Сброс", "Сбросить неделю токенов?", 
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.cfg["weekly_used"] = 0
            self.cfg["weekly_reset_time"] = time.time() + WEEKLY_DURATION
            self._save_cfg()
            self._update_ui_state()
            self.show_toast("Недельный лимит сброшен")

    # ── Автозапуск ───────────────────────────────────────────────────────────
    def _startup_lnk(self):
        return os.path.join(os.environ["APPDATA"],
                            "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                            f"Desktop_{TARGET}_Widget.lnk")

    def _startup_exists(self):
        return os.path.exists(self._startup_lnk())

    def _toggle_startup(self):
        lnk = self._startup_lnk()
        if os.path.exists(lnk):
            try:
                os.remove(lnk)
                self.show_toast("Автозапуск отключен")
            except Exception:
                self.show_toast("Ошибка отключения автозапуска")
        else:
            try:
                target = sys.executable  # pythonw.exe
                script = os.path.abspath(__file__)
                args = f'"{script}" --target {TARGET}'
                ps_cmd = f'$sh = New-Object -ComObject WScript.Shell; $lnk = $sh.CreateShortcut("{lnk}"); $lnk.TargetPath = "{target}"; $lnk.Arguments = "{args}"; $lnk.WorkingDirectory = "{os.path.dirname(script)}"; $lnk.IconLocation = "shell32.dll,238"; $lnk.Save()'
                subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
                self.show_toast("Автозапуск включен")
            except Exception:
                self.show_toast("Ошибка включения автозапуска")

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            if (self.windowState() & Qt.WindowState.WindowMinimized) and not getattr(self, "manually_hidden", False):
                QTimer.singleShot(250, self.showNormal)
        super().changeEvent(event)

    def closeEvent(self, event):
        if hasattr(self, "sync_worker"):
            self.sync_worker.stop()
            self.sync_worker.wait()
        QApplication.quit()
        super().closeEvent(event)


if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)  # не завершать процесс, когда всё свёрнуто в трей/бейдж
        widget = UniversalBudgetWidget()
        
        # Определяем, нужно ли показывать окно сразу.
        # Если пользователь ранее свернул виджет в мини-бейдж (manually_hidden),
        # это состояние сохранено в cfg и должно пережить перезапуск процесса
        # (watchdog перезапускает widget.py при каждом сворачивании/восстановлении
        # целевого приложения) - поэтому не разворачиваем окно на полный размер.
        if True:
            if widget.manually_hidden:
                widget.mini_badge = MiniBadge(widget)
                widget.mini_badge.update_values(
                    getattr(widget, "_last_session_pct", 0),
                    getattr(widget, "_last_weekly_pct", 0),
                    argon_pct=getattr(widget, "_last_argon_pct", None),
                )
                widget.mini_badge.show_near_tray()
                widget._update_ui_state()
                widget.hide()
            else:
                widget.show()
        else:
            widget.hide()
            
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        with open(os.path.join(os.environ.get('APPDATA',''), 'AIWidgetDock', 'engine', 'error.txt'), "w", encoding="utf-8") as f:
            f.write(f"Ошибка при работе виджета: {e}\n")
            traceback.print_exc(file=f)
        sys.exit(1)
