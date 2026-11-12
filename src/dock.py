# -*- coding: utf-8 -*-
"""AIWidgetDock — компактный виджет лимитов ИИ (Claude, Codex, Antigravity, Grok).
Один блок: строка ИИ появляется, только пока его программа запущена.
Движок чтения лимитов — тот же, что у виджетов DesktopClaudeWidget (engine.py)."""
import sys, os, re, json, time, glob, csv, threading, subprocess, importlib.util, urllib.request, tempfile, shutil
from datetime import datetime

APP = 'AIWidgetDock'
VERSION = '1.3.3'
REPO = 'ivankoval921-bit/AIWidgetDock'
DATA = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), APP)
os.makedirs(os.path.join(DATA, 'engine'), exist_ok=True)
RES = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
CFG_FILE = os.path.join(DATA, 'settings.json')
STATS_FILE = os.path.join(DATA, 'stats.csv')
LOG_FILE = os.path.join(DATA, 'dock.log')

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QObject, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap, QPen, QPainterPath, QFontMetrics, QIcon
from PyQt6.QtWidgets import (QApplication, QWidget, QMenu, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QSlider, QCheckBox, QPushButton, QFormLayout, QMessageBox, QTextBrowser, QSystemTrayIcon, QSpinBox, QInputDialog)


def log(m):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2_000_000:
            os.remove(LOG_FILE)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S ') + m + '\n')
    except Exception:
        pass


DEFAULTS = {'font': 'Segoe UI', 'bold': False, 'italic': False, 'upper': False, 'size': 12, 'scale': 100,
            'icon': 18, 'bg': 'black', 'bg_alpha': 85, 'x': -1, 'y': -1, 'first_run': True,
            'show': {'claude': True, 'codex': True, 'antigravity': True, 'grok': True},
            'chat_line': True, 'sound': True,
            'chat_limit': {'claude': 400, 'codex': 400, 'antigravity': 500, 'grok': 400},
            'ttl': {'claude': 'auto', 'codex': 'auto'}, 'autostart_init': False, 'lnk_made': False, 'ui': {}, 'no_auto_update': False}
TTLS = [('Авто', 'auto'), ('5 минут', '300'), ('10 минут', '600'), ('1 час', '3600')]
FONTS = ['Segoe UI', 'Segoe UI Semibold', 'Arial', 'Verdana', 'Tahoma', 'Calibri', 'Consolas', 'Bahnschrift',
         'Trebuchet MS', 'Roboto', 'Cascadia Mono', 'Georgia']


# ---------- конструктор вида: готовые наборы, темы, элементы ----------
UI_DEFAULT = {'shape': 'stack', 'icon': True, 'dot': True, 'session': True, 'week': True, 'chat': True,
              'pct': True, 'time': True, 'date': True, 'cache': True, 'tokens': True,
              'bars': False, 'bar_pos': 'right', 'bar_w': 36, 'time_short': False, 'theme': 'black', 'preset': 'Стандарт', 'chat_pos': 'side'}
_NOTXT = {'chat': False, 'time': False, 'date': False, 'dot': False}
PRESETS = [   # (название, что меняется относительно «Стандарта»)
    ('Стандарт', {}),
    ('Стандарт: чат третьей строкой', {'chat_pos': 'row'}),
    ('Стандарт с линиями', {'bars': True}),
    ('Линии под цифрами', {'bars': True, 'bar_pos': 'below', 'chat': False, 'date': False}),
    ('Без строки чата', {'chat': False}),
    ('Проценты и время', {'chat': False, 'date': False}),
    ('Только проценты', {'chat': False, 'time': False, 'date': False}),
    ('Только 5 часов', {'week': False, 'chat': False, 'date': False}),
    ('Проценты и линии', {'chat': False, 'time': False, 'date': False, 'bars': True}),
    ('Значок и линии, без цифр', {'chat': False, 'dot': False, 'bars': True, 'bar_pos': 'only', 'bar_w': 70}),
    ('Узкий столбик: проценты', dict(_NOTXT, week=False)),
    ('Полоса: проценты', dict(_NOTXT, shape='strip', week=False)),
    ('Полоса: 5 часов и неделя', dict(_NOTXT, shape='strip')),
    ('Полоса: проценты и линии', dict(_NOTXT, shape='strip', week=False, bars=True, bar_pos='below')),
    ('Полоса: значки и линии', {'shape': 'strip', 'chat': False, 'dot': False, 'bars': True, 'bar_pos': 'only', 'bar_w': 34}),
    ('Квадрат: проценты', dict(_NOTXT, shape='grid', week=False)),
    ('Квадрат: проценты и линии', dict(_NOTXT, shape='grid', week=False, bars=True, bar_pos='below')),
    ('Квадрат: полные строки', {'shape': 'grid', 'chat': False, 'date': False}),
]
THEMES = {
    'black': {'label': 'Чёрная', 'bg': (13, 15, 18), 'border': (42, 45, 51), 'sep': (232, 232, 232, 190), 'track': (255, 255, 255, 45), 'grey': '#9aa0a6', 'darken': False},
    'green': {'label': 'Зелёная', 'bg': (9, 30, 20), 'border': (34, 98, 62), 'sep': (140, 210, 165, 170), 'track': (255, 255, 255, 45), 'grey': '#9fb8a8', 'darken': False},
    'gray': {'label': 'Серая', 'bg': (54, 57, 64), 'border': (92, 96, 105), 'sep': (215, 217, 222, 160), 'track': (255, 255, 255, 50), 'grey': '#b4b8bf', 'darken': False},
    'blue': {'label': 'Синяя', 'bg': (12, 21, 42), 'border': (40, 66, 118), 'sep': (160, 188, 235, 160), 'track': (255, 255, 255, 45), 'grey': '#9db0d0', 'darken': False},
    'light': {'label': 'Светлая', 'bg': (246, 247, 249), 'border': (196, 200, 207), 'sep': (60, 64, 70, 110), 'track': (0, 0, 0, 40), 'grey': '#5f6368', 'darken': True},
}
ELEMENTS = [('icon', 'Значок ИИ'), ('dot', 'Цветная точка у цифр'), ('session', 'Строка 5 часов'), ('week', 'Строка недели'),
            ('chat', 'Строка чата'), ('pct', 'Проценты'), ('time', 'Время до сброса 5 часов'), ('date', 'Дата сброса недели'),
            ('cache', 'Кэш (таймер)'), ('tokens', 'Токены чата'), ('bars', 'Тонкие линии-индикаторы'),
            ('time_short', 'Короткое время (4ч вместо 4:31)')]


def fmt_short(ts):
    s = ts - time.time()
    if s <= 0:
        return '0м'
    if s >= 48 * 3600:
        return '%dд' % (s // 86400)
    if s >= 3600:
        return '%dч' % (s // 3600)
    return '%dм' % max(1, int(s // 60))


def load_cfg():
    c = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CFG_FILE, encoding='utf-8') as f:
            c.update(json.load(f))
    except Exception:
        pass
    return c


def save_cfg(c):
    try:
        with open(CFG_FILE, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log('save cfg: %s' % e)


# ---------- шкала: 5 зон по 20% (израсходовано) ----------
ZONES = ['#2fb35a', '#e0b400', '#f07a16', '#e8203c']


def zone_color(pct):
    try:
        p = float(pct)
    except Exception:
        return QColor('#9aa0a6')
    return QColor(ZONES[0 if p < 25 else 1 if p < 55 else 2 if p < 90 else 3])   # 0–25 зелёная, 25–55 жёлтая, 55–90 оранжевая, 90+ красная


# ---------- плавные цвета столбиков в окне подробностей: (процент, цвет) ----------
LIM_STOPS = [(0, '#2fb35a'), (18, '#2fb35a'), (32, '#e0b400'), (50, '#e0b400'), (62, '#f07a16'), (82, '#f07a16'), (92, '#e8203c')]
TOK_STOPS = [(0, '#2fb35a'), (30, '#2fb35a'), (40, '#e0b400'), (62, '#e0b400'), (75, '#f07a16'), (82, '#f07a16'), (90, '#e8203c')]
CACHE_STOPS = [(0, '#2fb35a'), (35, '#2fb35a'), (50, '#e0b400'), (65, '#e0b400'), (75, '#f07a16'), (83, '#f07a16'), (90, '#e8203c')]   # по доле ушедшего времени


def smooth_color(pct, stops):
    try:
        p = float(pct)
    except Exception:
        return QColor('#9aa0a6')
    if p <= stops[0][0]:
        return QColor(stops[0][1])
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if p <= p1:
            t = (p - p0) / float(p1 - p0); a = QColor(c0); b = QColor(c1)
            return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t), int(a.blue() + (b.blue() - a.blue()) * t))
    return QColor(stops[-1][1])


# ---------- чат: 4 зоны по заполнению окна (доли предела: 150/300/350 из 400) ----------
TOK_ZONES = ['#2fb35a', '#e0b400', '#f07a16', '#e8203c']


def fmt_k(n):
    return '%dk' % round(n / 1000.0)


def proc_running(names, out=None):
    try:
        if out is None:
            out = subprocess.check_output(['tasklist', '/FO', 'CSV', '/NH'], creationflags=0x08000000).decode('utf-8', 'ignore').lower()
        return any(('"%s"' % n) in out for n in names)
    except Exception:
        return False


def window_running(names, out):
    """Программа запущена И у неё есть видимое окно. После закрытия окна Antigravity остаются фоновые
    процессы без окон: по одному факту процесса виджет считал бы его запущенным."""
    import ctypes
    from ctypes import wintypes
    try:
        pids = set()
        for ln in out.splitlines():
            p = ln.split('","')
            if len(p) >= 2 and p[0].strip('"') in names:
                pids.add(int(p[1].strip('"')))
        if not pids:
            return False
        u = ctypes.windll.user32
        found = [False]
        WNDENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if u.IsWindowVisible(hwnd):
                pid = wintypes.DWORD(); u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in pids:
                    if u.IsIconic(hwnd):   # свёрнутое окно — программа всё ещё открыта
                        found[0] = True; return False
                    r = wintypes.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r))
                    if r.right - r.left > 100 and r.bottom - r.top > 60 and r.left > -30000:
                        found[0] = True; return False
            return True
        u.EnumWindows(WNDENUM(cb), 0)
        return found[0]
    except Exception:
        return True   # при сбое проверки не прячем строку


def update_bat_text(new, exe, flag, target, name, prev, failed, heartbeat):
    """Текст bat для безопасной замены exe: если новая версия не запустилась, возвращается прежняя."""
    if heartbeat:   # версии с 1.3.0 сами отмечают успешный запуск файлом started.flag
        check = 'if exist "%s" goto ok' % flag
    else:           # старые версии: достаточно, что процесс жив через 12 секунд
        check = 'if %%k%% GEQ 12 (tasklist /FI "IMAGENAME eq %s" 2>nul | find /i "%s" >nul && goto ok)' % (name, name)
    t = r"""@echo off
chcp 65001 >nul
set n=0
:r
set /a n+=1
ping 127.0.0.1 -n 4 >nul
copy /y "@NEW@" "@EXE@" >nul 2>&1
if errorlevel 1 if %n% LSS 20 goto r
if errorlevel 1 goto fail
del "@FLAG@" >nul 2>&1
start "" "@EXE@"
set k=0
:w
set /a k+=1
ping 127.0.0.1 -n 2 >nul
@CHECK@
if %k% LSS 30 goto w
goto fail
:ok
del "@TARGET@" >nul 2>&1
exit /b 0
:fail
taskkill /F /IM "@NAME@" >nul 2>&1
ping 127.0.0.1 -n 3 >nul
set m=0
:b
set /a m+=1
copy /y "@PREV@" "@EXE@" >nul 2>&1
if errorlevel 1 if %m% LSS 10 (ping 127.0.0.1 -n 3 >nul & goto b)
move /y "@TARGET@" "@FAILED@" >nul 2>&1
start "" "@EXE@"
"""
    for k_, v_ in (('@NEW@', new), ('@EXE@', exe), ('@FLAG@', flag), ('@CHECK@', check), ('@TARGET@', target), ('@NAME@', name), ('@PREV@', prev), ('@FAILED@', failed)):
        t = t.replace(k_, v_)
    return t.replace('\n', '\r\n')


def listen_pids(ports):
    """PID процессов, слушающих указанные порты (браузеры виджета с remote-debugging)."""
    import ctypes
    from ctypes import wintypes
    res = set()
    f = ctypes.windll.iphlpapi.GetExtendedTcpTable
    size = wintypes.DWORD(0)
    f(None, ctypes.byref(size), False, 2, 3, 0)
    buf = ctypes.create_string_buffer(size.value + 64)
    size = wintypes.DWORD(size.value + 64)
    if f(buf, ctypes.byref(size), False, 2, 3, 0) != 0:
        return res
    n = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[0]
    arr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))
    for i in range(n):
        base = 1 + 6 * i
        v = arr[base + 2]
        if (((v & 0xFF) << 8) | ((v >> 8) & 0xFF)) in ports:
            res.add(arr[base + 5])
    return res


def hide_bridge_windows(ports):
    """Прячет окна служебных браузеров виджета (свёрнутые / за экраном), чтобы не висели на панели задач.
    Окно входа в аккаунт (видимое на экране) не трогаем."""
    import ctypes
    from ctypes import wintypes
    pids = listen_pids(ports)
    if not pids:
        return
    u = ctypes.windll.user32
    WNDENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        try:
            if u.IsWindowVisible(hwnd):
                pid = wintypes.DWORD(); u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in pids:
                    cls = ctypes.create_unicode_buffer(64); u.GetClassNameW(hwnd, cls, 64)
                    if cls.value == 'Chrome_WidgetWin_1':
                        r = wintypes.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r))
                        if u.IsIconic(hwnd) or r.left <= -30000:
                            u.ShowWindow(hwnd, 0)
        except Exception:
            pass
        return True
    u.EnumWindows(WNDENUM(cb), 0)


def make_start_shortcut():
    """Ярлык в меню «Пуск» — запасной способ запустить виджет."""
    try:
        if not getattr(sys, 'frozen', False):
            return
        lnk = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'AIWidgetDock.lnk')
        if os.path.exists(lnk):
            return
        ps = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.Save()" % (
            lnk, sys.executable, os.path.dirname(sys.executable))
        subprocess.run(['powershell', '-NoProfile', '-Command', ps], creationflags=0x08000000, timeout=30)
    except Exception as ex:
        log('start shortcut: %r' % ex)


# ---------- данные ----------
class AIState:
    def __init__(self, key, title, icon):
        self.key, self.title, self.icon = key, title, icon
        self.running = False
        self.s_pct = None; self.s_reset = 0.0
        self.w_pct = None; self.w_reset = 0.0
        self.ctx_used = None; self.ctx_max = None; self.ctx_ts = 0.0
        self.approx = False
        self.updated = 0.0
        self.source = ''
        self.ch_used = None; self.ch_ts = 0.0; self.ch_ttl = 0; self.ch_src = ''   # текущий чат: токены, время последнего хода
        self.ch_approx = False   # токены оценены по длине текста (Claude Desktop), а не взяты из журнала
        self.al_ts = 0.0; self.al_beep = 0.0; self.al_zone = -1


class Bus(QObject):
    changed = pyqtSignal()


def iso_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00')).timestamp()
    except Exception:
        return 0.0


def stat(ai, note=''):
    """Скрытая статистика для проверки расчётов: %APPDATA%\\AIWidgetDock\\stats.csv"""
    try:
        new = not os.path.exists(STATS_FILE)
        with open(STATS_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['time', 'ai', 'running', 'session_pct', 'session_reset', 'week_pct', 'week_reset', 'ctx_used', 'ctx_max', 'source', 'note'])
            fmt = lambda t: datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M') if t else ''
            w.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ai.key, int(ai.running), ai.s_pct, fmt(ai.s_reset), ai.w_pct,
                        fmt(ai.w_reset), ai.ctx_used, ai.ctx_max, ai.source, note])
    except Exception:
        pass


def load_engine(target):
    path = os.path.join(RES, 'engine.py')
    if RES not in sys.path:
        sys.path.insert(0, RES)
    saved = sys.argv
    sys.argv = ['engine', '--target', target]
    try:
        spec = importlib.util.spec_from_file_location('engine_' + target, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = saved


class Engine:
    """Потоки чтения лимитов (движок виджетов) + локальное чтение журналов Codex."""
    def __init__(self, states, bus):
        self.st, self.bus = states, bus
        self.workers = {}
        self.mods = {}
        self.sessions = []

    def start(self, key):
        if key in self.workers or key == 'codex':
            return
        try:
            m = self.mods.get(key) or load_engine(key)
            self.mods[key] = m
            if key == 'claude':
                w = m.ClaudeSyncWorker()
                w.context_path = ''
                w.context_auto = True
                w.context_updated.connect(lambda d, k=key: self.on_ctx(k, d))
                w.sessions_updated.connect(lambda lst: setattr(self, 'sessions', list(lst)))   # аккаунты для меню «Выбрать аккаунт»
            elif key == 'grok':
                w = m.GrokSyncWorker()
            else:
                w = m.AntigravitySyncWorker()
            w.usage_fetched.connect(lambda d, k=key: self.on_usage(k, d))
            w.start()
            self.workers[key] = w
            log('engine started: ' + key)
        except Exception as e:
            log('engine %s: %r' % (key, e))

    def stop(self, key):
        w = self.workers.pop(key, None)
        if w:
            try:
                w.is_running = False
            except Exception:
                pass

    def refresh(self):
        for w in self.workers.values():
            try:
                w.force_refresh = True
            except Exception:
                pass
        self.read_all()

    def read_all(self):
        self.read_codex()
        self.read_claude_chat()
        self.read_claude_web()

    @staticmethod
    def _est_tokens(obj):
        """Грубая оценка токенов по тексту: кириллица ~2,2 символа на токен, остальное ~3,8."""
        cyr = oth = 0
        stack = [obj]
        while stack:
            x = stack.pop()
            if isinstance(x, str):
                for ch in x:
                    if '\u0400' <= ch <= '\u04ff':
                        cyr += 1
                    else:
                        oth += 1
            elif isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                for k, v in x.items():
                    if k in ('text', 'thinking', 'input', 'content', 'partial_json') or isinstance(v, (list, dict)):
                        stack.append(v)
        return int(cyr / 2.2 + oth / 3.8)

    def read_claude_web(self):
        """Чаты Claude Desktop на ПК не пишутся. Берём последний чат с claude.ai через браузер виджета:
        время хода — точное (кэш живёт около часа после хода), токены — оценка по длине текста (знак ~)."""
        a = self.st['claude']
        now = time.time()
        if not a.running or now - getattr(self, '_web_ts', 0) < 60 or getattr(self, '_web_busy', False):
            return
        if a.ch_used and a.ch_ts and now - a.ch_ts < 900 and not a.ch_approx:
            return   # есть свежий точный журнал Claude Code
        self._web_ts = now; self._web_busy = True

        def run():
            try:
                m = self.mods.get('claude')
                if not m:
                    return
                best = None   # самый свежий чат среди всех аккаунтов, в которые вошли браузеры виджета
                for pr in m.load_cdp_profiles():
                    try:
                        orgs = m.cdp_fetch_json(pr['port'], '/api/organizations')
                        for o in orgs:
                            ou = o.get('uuid')
                            if not ou:
                                continue
                            lst = m.cdp_fetch_json(pr['port'], '/api/organizations/%s/chat_conversations?limit=5' % ou)
                            for c in lst:
                                ts = iso_ts(c.get('updated_at'))
                                if best is None or ts > best[0]:
                                    best = (ts, pr['port'], ou, c.get('uuid'))
                    except Exception as ex:
                        log('claude web %s: %r' % (pr.get('port'), ex))
                if not best or not best[3]:
                    return
                ts, port, ou, cu = best
                conv = m.cdp_fetch_json(port, '/api/organizations/%s/chat_conversations/%s?tree=True&rendering_mode=messages&render_all_tools=true' % (ou, cu), timeout=20)
                msgs = conv.get('chat_messages') or []
                last = max([iso_ts(x.get('created_at')) for x in msgs] + [ts]) if msgs else ts
                tok = self._est_tokens([x.get('content') or x.get('text') for x in msgs])
                if tok and last and (not a.ch_ts or last >= a.ch_ts):
                    a.ch_used, a.ch_ts, a.ch_src, a.ch_approx = tok, last, 'claude.ai ~', True
                    self.bus.changed.emit()
            except Exception as ex:
                log('claude web: %r' % ex)
            finally:
                self._web_busy = False
        threading.Thread(target=run, daemon=True).start()

    def read_claude_chat(self):
        # Текущий чат Claude Code: ~/.claude/projects/*/*.jsonl — токены и время последнего хода (кэш)
        a = self.st['claude']
        try:
            base = os.path.join(os.path.expanduser('~'), '.claude', 'projects')
            cands = []
            for d in os.scandir(base):
                if not d.is_dir():
                    continue
                for e in os.scandir(d.path):
                    if e.name.endswith('.jsonl'):
                        cands.append((e.stat().st_mtime, e.path))
            cands.sort(reverse=True)
            used = ts = ttl = None
            best = ''
            for _, best in cands[:40]:   # самый свежий файл с реальными ходами (пустые и служебные пропускаем)
                for span in (600000, 6000000):
                    with open(best, 'rb') as fh:
                        fh.seek(0, 2); n = fh.tell(); fh.seek(max(0, n - span))
                        lines = fh.read().decode('utf-8', 'ignore').splitlines()
                    used = ts = ttl = None
                    tries = 0
                    for ln in reversed(lines):
                        if '"usage"' not in ln:
                            continue
                        tries += 1
                        if tries > 400:
                            break
                        try:
                            d = json.loads(ln)
                        except ValueError:
                            continue
                        m = d.get('message')
                        u = m.get('usage') if isinstance(m, dict) else None
                        if not u or d.get('isSidechain') or m.get('model') == '<synthetic>':
                            continue
                        tot = sum(int(u.get(x) or 0) for x in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens'))
                        if not tot:
                            continue
                        if used is None:
                            used, ts = tot, iso_ts(d.get('timestamp'))
                        cc = u.get('cache_creation') or {}
                        if int(cc.get('ephemeral_1h_input_tokens') or 0) > 0:
                            ttl = 3600
                        elif int(cc.get('ephemeral_5m_input_tokens') or 0) > 0:
                            ttl = 300
                        if ttl:
                            break
                    if used is not None:
                        break
                if used is not None:
                    break
            if used is None:
                return
            if not (a.ch_approx and a.ch_ts > (ts or 0)):   # оценку из браузера не затираем более старым журналом
                a.ch_used, a.ch_ts, a.ch_src, a.ch_approx = used, ts, os.path.basename(best), False
            if ttl:
                a.ch_ttl = ttl
        except Exception as e:
            log('claude chat: %r' % e)
        self.bus.changed.emit()

    def on_ctx(self, key, d):
        a = self.st[key]
        if not d or d.get('stale') or not d.get('tokens'):
            a.ctx_used = a.ctx_max = None; a.ctx_ts = 0.0
        else:
            a.ctx_ts = iso_ts(d.get('timestamp'))   # время последнего ответа = последний ход, от него идёт кэш
            a.ctx_used = int(d.get('tokens') or 0)
            a.ctx_max = int(d.get('limit') or 0) or None
        self.bus.changed.emit()

    def on_usage(self, key, d):
        a = self.st[key]
        try:
            if key == 'claude':
                fh, sd = d.get('five_hour') or {}, d.get('seven_day') or {}
                if fh or sd:
                    a.s_pct = float(fh.get('utilization') or 0); a.s_reset = iso_ts(fh.get('resets_at'))
                    a.w_pct = float(sd.get('utilization') or 0); a.w_reset = iso_ts(sd.get('resets_at'))
                    a.source = 'claude api'
                    if d.get('_file'):
                        a.source = 'файл Claude Desktop ~' + datetime.fromtimestamp(d.get('_ts') or 0).strftime('%H:%M') + ' — войдите в этот аккаунт в браузере виджета'
                        a.approx = True
                    else:
                        a.approx = False
            elif key == 'antigravity':
                resp = d.get('response') or {}
                for g in resp.get('groups', []) or []:
                    for b in g.get('buckets', []) or []:
                        bid = b.get('bucketId', '')
                        used = 100.0 - float((b.get('remaining') or {}).get('value', 1.0)) * 100.0
                        rs = float((b.get('resetTime') or {}).get('seconds', 0) or 0)
                        if bid == 'gemini-5h':
                            a.s_pct, a.s_reset = used, rs
                        elif bid == 'gemini-weekly':
                            a.w_pct, a.w_reset = used, rs
                if d.get('chat_tokens'):
                    a.ctx_used = int(d.get('chat_tokens') or 0); a.ctx_max = 1000000
                a.source = 'antigravity'
            elif key == 'grok':
                budget = 50000000
                a.approx = True
                a.s_pct = min(100.0, 100.0 * float(d.get('session_tokens') or 0) / budget)
                a.w_pct = min(100.0, 100.0 * float(d.get('weekly_tokens') or 0) / budget)
                if d.get('context_tokens'):
                    a.ctx_used = int(d.get('context_tokens')); a.ctx_max = int(d.get('context_window') or 500000)
                a.source = 'grok sessions'
            a.updated = time.time()
            stat(a)
        except Exception as e:
            log('usage %s: %r' % (key, e))
        self.bus.changed.emit()

    def read_codex(self):
        """Codex сам пишет лимиты и заполнение окна в ~/.codex/sessions/*.jsonl"""
        a = self.st['codex']
        try:
            files = glob.glob(os.path.join(os.path.expanduser('~'), '.codex', 'sessions', '**', '*.jsonl'), recursive=True)
            if not files:
                return
            f = max(files, key=os.path.getmtime)
            with open(f, 'rb') as fh:
                fh.seek(0, 2); n = fh.tell(); fh.seek(max(0, n - 400000))
                lines = fh.read().decode('utf-8', 'ignore').splitlines()
            for ln in reversed(lines):
                if '"token_count"' not in ln:
                    continue
                try:
                    obj = json.loads(ln); p = obj.get('payload') or {}
                except ValueError:
                    continue
                info, rl = p.get('info') or {}, p.get('rate_limits') or {}
                wins = [w for w in (rl.get('primary'), rl.get('secondary')) if w]
                five = next((w for w in wins if 0 < float(w.get('window_minutes') or 0) <= 330), None)
                week = next((w for w in wins if float(w.get('window_minutes') or 0) > 330), None)
                a.s_pct, a.s_reset = (float(five.get('used_percent') or 0), float(five.get('resets_at') or 0)) if five else (None, 0.0)
                if week:
                    a.w_pct, a.w_reset = float(week.get('used_percent') or 0), float(week.get('resets_at') or 0)
                lt = info.get('last_token_usage') or {}
                if info.get('model_context_window') and lt:
                    a.ctx_used = int(lt.get('input_tokens') or 0) + int(lt.get('output_tokens') or 0)
                    a.ctx_max = int(info['model_context_window'])
                    a.ch_used = a.ctx_used; a.ch_ts = iso_ts(obj.get('timestamp')); a.ch_src = os.path.basename(f)
                a.source = 'codex sessions'
                a.updated = time.time()
                stat(a)
                break
        except Exception as e:
            log('codex: %r' % e)
        self.bus.changed.emit()


def fmt_left(ts):
    if not ts:
        return ''
    s = int(ts - time.time())
    if s <= 0:
        return 'сейчас'
    h, m = s // 3600, (s % 3600) // 60
    return '%d:%02d' % (h, m) if h < 48 else '%d дн' % (h // 24)


def fmt_date(ts):
    return datetime.fromtimestamp(ts).strftime('%d.%m %Hч') if ts else ''


# ---------- окно подробностей ----------
class Details(QWidget):
    def __init__(self, dock, a):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)   # не Popup: Popup гаснет при любой смене активного окна
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet('QWidget{background:#0d0f12;color:#ddd;font:12px "Segoe UI"} QPushButton{background:#1b1e23;border:1px solid #444;border-radius:4px;padding:3px 8px}')
        self.dock = dock; self.a = a
        self.v = QVBoxLayout(self); self.v.setContentsMargins(10, 8, 10, 8); self.v.setSpacing(4)
        self._sig = None
        self.build()

    def signature(self):
        """Что видно в окне: перестраиваем, только если это изменилось (иначе окно мигает)."""
        a = self.a; ci = self.dock.chat_info(a)
        r = lambda x: None if x is None else round(x)
        return (r(a.s_pct), r(a.w_pct), a.ctx_used, a.ctx_max, int(time.time() // 60),
                (ci['used'] // 1000, ci['state'], ci['zone']) if ci else None)

    def build(self, force=False):
        sig = self.signature()
        if not force and sig == self._sig:
            return
        self._sig = sig
        self.setUpdatesEnabled(False)   # без промежуточных перерисовок: окно не «хлопает» при пересборке
        try:
            self._build_inner()
        finally:
            self.setUpdatesEnabled(True)

    @staticmethod
    def _clear(lay):
        while lay.count():
            it = lay.takeAt(0); w = it.widget(); sub = it.layout()
            if w is not None:
                w.setParent(None); w.deleteLater()
            elif sub is not None:
                Details._clear(sub)

    def _build_inner(self):
        """Заполнить окно свежими цифрами (вызывается при открытии и при обновлении данных)."""
        dock, a, v = self.dock, self.a, self.v
        self._clear(v)
        v.addWidget(QLabel('<b>%s</b>' % a.title))

        def bar(name, pct, extra, col=None, txt=None):
            if pct is None:
                return
            c = col or smooth_color(pct, LIM_STOPS).name()
            v.addWidget(QLabel('%s: <b style="color:%s">%s</b>' % (name, c, txt or ('%s%d%%' % ('~' if a.approx else '', round(pct))))))
            b = QLabel(); b.setFixedHeight(6)
            q = max(0.001, min(0.998, pct / 100.0))
            b.setStyleSheet('background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 %s,stop:%.3f %s,stop:%.3f #2a2d33,stop:1 #2a2d33);border-radius:3px' % (c, q, c, q + 0.001))
            v.addWidget(b)
            if extra:
                l = QLabel(extra); l.setStyleSheet('color:#999'); v.addWidget(l)
        bar('5-часовая сессия', a.s_pct, ('Сброс через %s (%s)' % (fmt_left(a.s_reset), datetime.fromtimestamp(a.s_reset).strftime('%H:%M'))) if a.s_reset else '')
        bar('Неделя', a.w_pct, ('Сброс: %s' % datetime.fromtimestamp(a.w_reset).strftime('%d.%m в %H:%M')) if a.w_reset else '')
        if a.ctx_used and a.ctx_max:
            bar('Окно контекста', 100.0 * a.ctx_used / a.ctx_max, '%d тыс. из %d тыс. токенов' % (a.ctx_used // 1000, a.ctx_max // 1000))
        ci = dock.chat_info(a)
        if ci:
            warn = ' — пора в новый чат!' if ci['zone'] == 3 else ''
            bar('Токены в этом чате', min(100.0, 100.0 * ci['used'] / ci['limit']),
                '%d тыс. из %d тыс. (предел меняется в настройках)%s' % (ci['used'] // 1000, ci['limit'] // 1000, warn),
                smooth_color(min(100.0, 100.0 * ci['used'] / ci['limit']), TOK_STOPS).name(), '%s%d тыс.' % ('~' if ci.get('approx') else '', ci['used'] // 1000))
            if ci['state'] == 'dead':
                v.addWidget(QLabel('Кэш: <b style="color:#8a8f98">остыл</b> — следующий ход заново читает весь чат, это дороже'))
            elif ci['state']:
                left = ci['left']
                bar('Кэш', 100.0 * left / ci['ttl'], 'Осталось %d мин из %d. Любой ход в чате обновляет кэш.' % (round(left / 60), round(ci['ttl'] / 60)),
                    smooth_color(100.0 - 100.0 * max(0.0, left) / ci['ttl'], CACHE_STOPS).name(), '%d мин' % max(1, round(left / 60)))
        if a.s_pct is None and a.w_pct is None:
            v.addWidget(QLabel('Нет данных. Правый клик → «Инструкция»: как подключить аккаунт.'))
        h = QHBoxLayout()
        b1 = QPushButton('Обновить'); b1.clicked.connect(lambda: (dock.engine.refresh(), QTimer.singleShot(2500, lambda: safe_call(self.build, True))))
        b2 = QPushButton('Инструкция'); b2.clicked.connect(lambda: (self.close(), dock.show_readme()))
        h.addWidget(b1); h.addWidget(b2); v.addLayout(h)
        if a.updated:
            u = QLabel('Обновлено %s · %s' % (datetime.fromtimestamp(a.updated).strftime('%H:%M:%S'), a.source)); u.setStyleSheet('color:#666;font-size:10px'); v.addWidget(u)
        self.adjustSize()

    def mouseDoubleClickEvent(self, e):   # двойной клик по окну подробностей — свернуть
        self.close()

    def closeEvent(self, e):
        if self.dock._det.get(self.a.key) is self:
            self.dock._det.pop(self.a.key, None)
        super().closeEvent(e)


# ---------- настройки ----------
class Settings(QDialog):
    def __init__(self, dock):
        super().__init__(dock)
        self.dock = dock; c = dock.cfg
        self.setWindowTitle('AIWidgetDock — настройки')
        f = QFormLayout(self)
        self.font = QComboBox(); self.font.addItems(FONTS); self.font.setCurrentText(c['font']); f.addRow('Шрифт', self.font)
        self.bold = QCheckBox('Жирный'); self.bold.setChecked(c['bold'])
        self.ital = QCheckBox('Курсив'); self.ital.setChecked(c['italic'])
        self.upper = QCheckBox('ПРОПИСНЫЕ'); self.upper.setChecked(c['upper'])
        h = QHBoxLayout()
        for x in (self.bold, self.ital, self.upper):
            h.addWidget(x)
        f.addRow('Начертание', h)

        def sl(lo, hi, val):
            s = QSlider(Qt.Orientation.Horizontal); s.setRange(lo, hi); s.setValue(int(val)); s.valueChanged.connect(self.apply); return s
        self.size = sl(9, 16, c['size']); f.addRow('Размер текста', self.size)
        self.scale = sl(70, 200, c['scale']); f.addRow('Масштаб виджета, %', self.scale)
        self.icon = sl(12, 32, c['icon']); f.addRow('Значки', self.icon)
        self.bg = QComboBox(); self.bg.addItems(['Чёрный', 'Полупрозрачный', 'Без фона (только цифры и значки)'])
        self.bg.setCurrentIndex({'black': 0, 'semi': 1, 'none': 2}.get(c['bg'], 0)); f.addRow('Фон', self.bg)
        self.alpha = sl(0, 100, c['bg_alpha']); f.addRow('Плотность фона, %', self.alpha)
        self.chat_cb = QCheckBox('Строка чата: токены и кэш'); self.chat_cb.setChecked(c.get('chat_line', True)); self.chat_cb.toggled.connect(self.apply); f.addRow('', self.chat_cb)
        self.snd_cb = QCheckBox('Звуковой сигнал (кэш остывает / чат заполнен)'); self.snd_cb.setChecked(c.get('sound', True)); self.snd_cb.toggled.connect(self.apply); f.addRow('', self.snd_cb)
        for k, t in (('claude', 'Claude'), ('codex', 'Codex'), ('antigravity', 'Antigravity'), ('grok', 'Grok')):
            sb = QSpinBox(); sb.setRange(50, 2000); sb.setSingleStep(50); sb.setSuffix(' тыс.')
            sb.setValue(int(c.get('chat_limit', {}).get(k, DEFAULTS['chat_limit'][k]))); sb.valueChanged.connect(self.apply)
            setattr(self, 'lim_' + k, sb); f.addRow('Предел чата: ' + t, sb)
        for k, t in (('claude', 'Claude'), ('codex', 'Codex')):
            cb = QComboBox()
            for lab, val in TTLS:
                cb.addItem(lab, val)
            cb.setCurrentIndex(max(0, cb.findData(str(c.get('ttl', {}).get(k, 'auto'))))); cb.currentIndexChanged.connect(self.apply)
            setattr(self, 'ttl_' + k, cb); f.addRow('Срок кэша: ' + t, cb)
        for k, t in (('claude', 'Claude'), ('codex', 'Codex'), ('antigravity', 'Antigravity'), ('grok', 'Grok')):
            cb = QCheckBox('Показывать ' + t); cb.setChecked(c['show'].get(k, True)); cb.toggled.connect(self.apply); setattr(self, 'sh_' + k, cb); f.addRow('', cb)
        self.font.currentIndexChanged.connect(self.apply); self.bg.currentIndexChanged.connect(self.apply)
        for w in (self.bold, self.ital, self.upper):
            w.toggled.connect(self.apply)
        hb = QHBoxLayout(); r = QPushButton('Как было'); r.clicked.connect(self.reset); ok = QPushButton('Готово'); ok.clicked.connect(self.accept)
        hb.addWidget(r); hb.addWidget(ok); f.addRow(hb)

    def apply(self, *_):
        c = self.dock.cfg
        c.update({'font': self.font.currentText(), 'bold': self.bold.isChecked(), 'italic': self.ital.isChecked(), 'upper': self.upper.isChecked(),
                  'size': self.size.value(), 'scale': self.scale.value(), 'icon': self.icon.value(), 'bg': ['black', 'semi', 'none'][self.bg.currentIndex()],
                  'bg_alpha': self.alpha.value()})
        for k in ('claude', 'codex', 'antigravity', 'grok'):
            c['show'][k] = getattr(self, 'sh_' + k).isChecked()
        c['chat_line'] = self.chat_cb.isChecked(); c['sound'] = self.snd_cb.isChecked()
        c['chat_limit'] = {k: getattr(self, 'lim_' + k).value() for k in ('claude', 'codex', 'antigravity', 'grok')}
        c['ttl'] = {k: getattr(self, 'ttl_' + k).currentData() for k in ('claude', 'codex')}
        save_cfg(c); self.dock.check_procs(); self.dock.relayout()

    def reset(self):
        keep = {k: self.dock.cfg[k] for k in ('x', 'y', 'first_run')}
        self.dock.cfg = json.loads(json.dumps(DEFAULTS)); self.dock.cfg.update(keep)
        save_cfg(self.dock.cfg); self.dock.relayout(); self.close()


README = """<h3>AIWidgetDock — виджет лимитов ИИ</h3>
<p>Маленький блок на рабочем столе. Для каждого запущенного ИИ — две строки:
<b>верхняя</b> — 5-часовой лимит (процент и сколько осталось до сброса: 2:15 = 2 ч 15 мин),
<b>нижняя</b> — недельный лимит (процент, дата и час сброса).
Цвет цифр: зелёный 0–25%, жёлтый 25–55%, оранжевый 55–90%, красный от 90%. Тонкие линии, длина которых равна проценту, включаются в конструкторе вида.</p>
<p>Строка ИИ появляется, когда его программа запущена, и пропадает, когда она закрыта.</p>
<h4>Установка</h4><ol><li>Скопируйте <b>AIWidgetDock.exe</b> в любую папку (например D:\\Programs) и запустите.</li>
<li>Чтобы запускался вместе с Windows: правый клик по виджету → «Запускать с Windows».</li></ol>
<h4>Строка «чат» (новое в 1.1)</h4>
<p>Справа от лимитов, за вертикальной перегородкой, показан <b>текущий чат</b>: сверху токены («187k/400k»), снизу обратный отсчёт кэша («кэш 54м»). Старый вид, где чат идёт третьей строкой, включается в конструкторе: набор «Стандарт: чат третьей строкой».
<b>Кэш</b> — сколько ещё минут ИИ читает ваш чат из дешёвой памяти. Каждый ход обновляет отсчёт. Когда остаётся меньше четверти срока, цифра становится красной и мигает,
за последние минуты появляется «ХОД!»: до 10 раз с интервалом 5 секунд звучит сигнал. Клик по виджету останавливает сигнал. Сделайте любой ход в чате (хоть «подожди, думаю»). Если кэш остыл, следующий ход дороже.
<b>Токены</b> — заполнение окна чата: зелёный до 150k, жёлтый до 300k, оранжевый до 350k, красный выше (для предела 400k; доли те же при другом пределе).
В красной зоне цифра мигает: пора переходить в новый чат. Пределы и срок кэша задаются в настройках.
Claude Code и Codex считаются автоматически из журналов. Для чатов Claude Desktop журнала на ПК нет: виджет берёт последний чат с claude.ai через свой браузер. Время хода (и кэш-таймер) точное, токены — оценка по длине текста, помечена знаком «~». У остальных токены обновляются кнопкой «Обновить», а кэш-таймера нет.</p>
<h4>Подключение аккаунтов</h4><ul>
<li><b>Claude</b> — откройте Claude Desktop и войдите в аккаунт (или войдите на claude.ai в Chrome / Firefox). Виджет найдёт вход сам.</li>
<li><b>Codex</b> — войдите в приложение Codex и сделайте хотя бы один запрос: Codex сам записывает лимиты, виджет их читает.</li>
<li><b>Antigravity</b> — откройте Antigravity и войдите в аккаунт Google.</li>
<li><b>Grok</b> — точных лимитов у Grok нет, показывается примерный расход со знаком «~».</li></ul>
<h4>Конструктор вида (новое в 1.3)</h4>
<p>Правый клик → «Конструктор вида…». Вид собирается под себя:
<b>готовые наборы</b> (17 вариантов: от полного с кэшем и линиями до одних процентов или значков с линиями),
<b>форма</b> (столбик, полоса в один ряд, квадрат по два в ряд),
<b>тема</b> (чёрная, зелёная, серая, синяя, светлая),
и отдельные <b>переключатели</b>: значок, цветная точка, строки 5 часов / недели / чата, проценты, время, дата, кэш, токены, тонкие линии (справа, под цифрами или вместо цифр), короткое время (4ч).
Любое ручное изменение превращает набор в «Свой». «Как было» возвращает «Стандарт» (без линий). Клик по блоку ИИ в любой форме открывает полную версию с подробностями.</p>
<h4>Откат версии</h4>
<p>Правый клик → «Откатиться на другую версию…»: выберите любую выпущенную версию, виджет скачает её и перезапустится. Настройки сохраняются. После отката автообновление выключается, чтобы виджет не вернулся на новую версию сам; включить обратно: галочка «Автообновление» в меню.</p>
<h4>Работа в фоне</h4><p>Виджет живёт в трее и сам запускается вместе с Windows. Он показывается, только пока запущен хоть один ИИ, и пропадает, когда все закрыты; при следующем запуске ИИ появляется снова. «Свернуть» прячет его до запуска ИИ, «Закрыть полностью» выключает совсем (запуск: меню «Пуск» → AIWidgetDock). Служебные окна браузера для чтения лимитов Claude скрыты и не мешают.</p>
<h4>Управление</h4><ul><li>Перетащить — левой кнопкой за блок. Клик по строке — подробности: сессия, неделя, окно контекста (где программа его отдаёт).</li>
<li>Клик (или двойной клик) по блоку ИИ открывает подробности; ещё один клик по тому же блоку или двойной клик по самому окну закрывает их. Окно держится открытым и обновляется само, только когда меняются цифры.</li>
<li>Правый клик — меню: Настройки (шрифт, жирность, курсив, размер, масштаб, фон: чёрный / полупрозрачный / без фона), Обновить, «Конструктор вида…», «Подробности: держать открытыми», «Линии-индикаторы», «Выбрать аккаунт Claude» (авто или конкретный), «Аккаунты: подключить / переключить» (вход в ещё один аккаунт Claude через браузер), «Закрепить положение», Проверить обновление, «Откатиться на другую версию…», «Автообновление», Инструкция, «Свернуть», «Закрыть полностью».</li></ul>
<p>Настройки и статистика: %APPDATA%\\AIWidgetDock (stats.csv — журнал цифр для проверки расчётов).</p>"""


# ---------- конструктор вида ----------
class Builder(QDialog):
    def __init__(self, dock):
        super().__init__(dock)
        self.dock = dock; self._lock = False
        self.setWindowTitle('AIWidgetDock — конструктор вида')
        f = QFormLayout(self)
        self.preset = QComboBox(); self.preset.addItems([n for n, _ in PRESETS] + ['Свой']); self.preset.activated.connect(self.on_preset)
        f.addRow('Готовый набор', self.preset)
        self.shape = QComboBox()
        for key, lab in (('stack', 'Столбик: ИИ друг под другом'), ('strip', 'Полоса: ИИ в один ряд'), ('grid', 'Квадрат: ИИ по два в ряд')):
            self.shape.addItem(lab, key)
        self.shape.activated.connect(self.on_change); f.addRow('Форма', self.shape)
        self.theme = QComboBox()
        for key, t in THEMES.items():
            self.theme.addItem(t['label'], key)
        self.theme.activated.connect(self.on_theme); f.addRow('Тема', self.theme)
        self.cb = {}
        for key, lab in ELEMENTS:
            c = QCheckBox(lab); c.toggled.connect(self.on_change); self.cb[key] = c; f.addRow('', c)
        self.chatpos = QComboBox()
        for key, lab in (('side', 'Чат справа отдельным блоком: токены и кэш'), ('row', 'Чат третьей строкой')):
            self.chatpos.addItem(lab, key)
        self.chatpos.activated.connect(self.on_change); f.addRow('Положение чата', self.chatpos)
        self.bpos = QComboBox()
        for key, lab in (('right', 'Линия справа от цифр'), ('below', 'Линия под цифрами'), ('only', 'Только линии, без цифр')):
            self.bpos.addItem(lab, key)
        self.bpos.activated.connect(self.on_change); f.addRow('Положение линий', self.bpos)
        self.bw = QSlider(Qt.Orientation.Horizontal); self.bw.setRange(20, 120); self.bw.valueChanged.connect(self.on_change); f.addRow('Длина линии', self.bw)
        hb = QHBoxLayout(); r = QPushButton('Как было (Стандарт)'); r.clicked.connect(self.reset); ok = QPushButton('Готово'); ok.clicked.connect(self.accept)
        hb.addWidget(r); hb.addWidget(ok); f.addRow(hb)
        self.sync()

    def sync(self):
        self._lock = True
        d = self.dock
        names = [n for n, _ in PRESETS]
        self.preset.setCurrentText(d.ui('preset') if d.ui('preset') in names else 'Свой')
        self.shape.setCurrentIndex(max(0, self.shape.findData(d.ui('shape'))))
        self.theme.setCurrentIndex(max(0, self.theme.findData(d.ui('theme'))))
        self.bpos.setCurrentIndex(max(0, self.bpos.findData(d.ui('bar_pos'))))
        self.chatpos.setCurrentIndex(max(0, self.chatpos.findData(d.ui('chat_pos'))))
        self.bw.setValue(int(d.ui('bar_w')))
        for key, c in self.cb.items():
            c.setChecked(bool(d.ui(key)))
        self._lock = False

    def on_change(self, *_):
        if self._lock:
            return
        v = {k: c.isChecked() for k, c in self.cb.items()}
        v['shape'] = self.shape.currentData(); v['bar_pos'] = self.bpos.currentData(); v['bar_w'] = self.bw.value(); v['chat_pos'] = self.chatpos.currentData()
        self.dock.set_ui(**v); self.sync()

    def on_preset(self, i):
        n = self.preset.itemText(i)
        if n != 'Свой':
            self.dock.apply_preset(n); self.sync()

    def on_theme(self, i):
        self.dock.set_ui(theme=self.theme.currentData(), preset=self.dock.ui('preset'))

    def reset(self):
        self.dock.apply_preset('Стандарт'); self.sync()


# ---------- главный блок ----------
class Dock(QWidget):
    ORDER = ['claude', 'codex', 'antigravity', 'grok']

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.cfg = load_cfg()
        if 'bars' in self.cfg:   # старая настройка 1.2.3 переезжает в конструктор
            u = dict(self.cfg.get('ui', {})); u.setdefault('bars', bool(self.cfg.pop('bars'))); self.cfg['ui'] = u
        self.bus = Bus(); self.bus.changed.connect(self.relayout)
        icons = {k: os.path.join(RES, 'assets', 'icon_%s.png' % k) for k in self.ORDER}
        self.st = {k: AIState(k, t, QPixmap(icons[k]) if os.path.exists(icons[k]) else QPixmap())
                   for k, t in (('claude', 'Claude'), ('codex', 'Codex'), ('antigravity', 'Antigravity'), ('grok', 'Grok'))}
        self.engine = Engine(self.st, self.bus)
        self.rows = []; self.cells = []; self._seps = []; self._force_show = False; self._det = {}; self._alarm = {}; self._dbl = False; self._drag = None; self._moved = False; self._procs = {}; self._user_hidden = False; self._ready = False
        self.t_proc = QTimer(self); self.t_proc.timeout.connect(self.check_procs); self.t_proc.start(5000)
        self.t_codex = QTimer(self); self.t_codex.timeout.connect(self.engine.read_all); self.t_codex.start(20000)
        self.t_tick = QTimer(self); self.t_tick.timeout.connect(self.relayout); self.t_tick.start(30000)
        self.t_blink = QTimer(self); self.t_blink.timeout.connect(self.tick); self.t_blink.start(1000)
        self.t_hide = QTimer(self); self.t_hide.timeout.connect(self.hide_bridges); self.t_hide.start(3000)
        self.t_stat = QTimer(self); self.t_stat.timeout.connect(lambda: [stat(a, 'hourly') for a in self.st.values() if a.running]); self.t_stat.start(3600000)
        self.tray = QSystemTrayIcon(QIcon(icons['claude']), self); self.tray.setToolTip('AIWidgetDock'); self.tray.setContextMenu(self.menu()); self.tray.show(); self.tray.activated.connect(self.on_tray)
        self.check_procs(); self.engine.read_all(); self.relayout()
        if self.cfg['x'] >= 0:
            self.move(self.cfg['x'], self.cfg['y'])
        else:
            g = QApplication.primaryScreen().availableGeometry(); self.move(g.right() - self.width() - 20, g.bottom() - self.height() - 70)
        if self.cfg.get('first_run'):
            self.cfg['first_run'] = False; save_cfg(self.cfg); QTimer.singleShot(800, self.show_readme)
        QTimer.singleShot(15000, lambda: self.check_update(False))
        if getattr(sys, 'frozen', False) and not self.cfg.get('autostart_init'):   # автозапуск с Windows включён по умолчанию
            self.cfg['autostart_init'] = True; save_cfg(self.cfg); self.set_autostart(True)
        if not self.cfg.get('lnk_made'):
            self.cfg['lnk_made'] = True; save_cfg(self.cfg); threading.Thread(target=make_start_shortcut, daemon=True).start()
        self.t_upd = QTimer(self); self.t_upd.timeout.connect(lambda: self.check_update(False)); self.t_upd.start(6 * 3600 * 1000)   # и дальше раз в 6 часов

    def check_procs(self):
        """Строка ИИ видна, только пока его программа работает."""
        def run():
            try:
                out = subprocess.check_output(['tasklist', '/FO', 'CSV', '/NH'], creationflags=0x08000000).decode('utf-8', 'ignore').lower()
            except Exception:
                out = ''
            r = {'claude': proc_running(['claude.exe'], out), 'codex': proc_running(['codex.exe', 'chatgpt.exe', 'openai.codex.exe'], out),
                 'antigravity': window_running(['antigravity.exe'], out), 'grok': proc_running(['grok.exe'], out)}
            if not r['grok']:
                d = os.path.join(os.path.expanduser('~'), '.grok', 'sessions')
                if os.path.isdir(d):
                    fs = glob.glob(os.path.join(d, '**', '*'), recursive=True)
                    r['grok'] = bool(fs) and time.time() - max(os.path.getmtime(x) for x in fs) < 1800
            self._procs = r
        t = threading.Thread(target=run, daemon=True); t.start(); t.join(4)
        ch = False
        for k, a in self.st.items():
            on = bool(self._procs.get(k)) and self.cfg['show'].get(k, True)
            if on != a.running:
                a.running = on; ch = True; stat(a, 'start' if on else 'stop')
                if on:
                    self._user_hidden = False
                if on:
                    self.engine.start(k)
                else:
                    self.engine.stop(k)
        if ch:
            self.relayout()

    def fonts(self):
        k = self.cfg['scale'] / 100.0
        f = QFont(self.cfg['font']); f.setPixelSize(max(6, int(self.cfg['size'] * k)))
        f.setBold(self.cfg['bold']); f.setItalic(self.cfg['italic'])
        if self.cfg['upper']:
            f.setCapitalization(QFont.Capitalization.AllUppercase)
        return f, k

    GREY = QColor('#9aa0a6')

    def ttl_for(self, a):
        v = str(self.cfg.get('ttl', {}).get(a.key, 'auto'))
        if v != 'auto':
            try:
                return int(v)
            except ValueError:
                pass
        return (a.ch_ttl or 3600) if a.key == 'claude' else 600

    def chat_info(self, a):
        """Текущий чат: токены, зона, остаток кэша. None — нечего показывать."""
        if not self.cfg.get('chat_line', True):
            return None
        now = time.time()
        if a.ch_used and a.ch_ts and now - a.ch_ts <= 12 * 3600:
            used, ts, appr = a.ch_used, a.ch_ts, a.ch_approx
        elif a.key != 'codex' and a.ctx_used:
            used, ts, appr = a.ctx_used, (a.ctx_ts if (a.ctx_ts and now - a.ctx_ts <= 12 * 3600) else 0.0), False
        else:
            return None
        limit = max(1, int(self.cfg.get('chat_limit', {}).get(a.key, 400))) * 1000
        r = used / float(limit)
        ci = {'used': used, 'limit': limit, 'zone': 0 if r < 0.375 else 1 if r < 0.75 else 2 if r < 0.875 else 3,
              'ts': ts, 'ttl': 0, 'left': None, 'state': None, 'approx': bool(appr and ts)}
        if ts:
            ttl = self.ttl_for(a); left = ts + ttl - now
            ci.update(ttl=ttl, left=left, state='dead' if left <= 0 else 'beep' if left <= min(300, ttl * 0.1) else 'warn' if left <= ttl * 0.25 else 'ok')
        return ci

    def chat_segs(self, ci, cache=True, tokens=True):
        tc = QColor(TOK_ZONES[ci['zone']]); segs = []
        st = ci['state']
        if cache and st == 'dead':
            segs.append(('кэш остыл', QColor('#8a8f98'), False))
        elif cache and st:
            left = ci['left']
            t = ('%dм' % -(-int(left) // 60)) if left >= 600 else '%d:%02d' % (int(left) // 60, int(left) % 60)
            if st == 'beep':
                segs.append(('ХОД! ' + t, QColor('#e8203c'), True))
            elif st == 'warn':
                segs.append(('кэш ' + t, QColor('#e8203c'), True))
            else:
                segs.append(('кэш ' + t, QColor('#2fb35a'), False))
        if cache and segs and tokens:
            segs.append(('  ', tc, False))
        if tokens:
            segs.append(('%s%s/%s%s' % ('~' if ci.get('approx') else '', fmt_k(ci['used']), fmt_k(ci['limit']), '!' if ci['zone'] == 3 else ''), tc, ci['zone'] == 3))
        return segs

    def ui(self, k):
        return self.cfg.get('ui', {}).get(k, UI_DEFAULT[k])

    def theme(self):
        return THEMES.get(self.ui('theme'), THEMES['black'])

    def set_ui(self, **kw):
        u = dict(self.cfg.get('ui', {})); u.update(kw)
        if 'preset' not in kw:
            u['preset'] = 'Свой'
        self.cfg['ui'] = u; save_cfg(self.cfg); self.relayout(); self.update()

    def apply_preset(self, name):
        u = dict(UI_DEFAULT); u.update(dict(PRESETS).get(name, {})); u['theme'] = self.ui('theme'); u['preset'] = name
        self.cfg['ui'] = u; save_cfg(self.cfg); self.relayout(); self.update()

    def cell_model(self, a):
        """Колонки блока ИИ: [[строки лимитов], [строки чата]]. Строка: (цвет, [(текст, цвет, мигает)], инфо чата, процент для линии, вид)."""
        ap = '~' if a.approx else ''
        only = self.ui('bars') and self.ui('bar_pos') == 'only'   # режим «только линии»: цифр нет
        short = self.ui('time_short')
        lim = []

        def one(pct, extra):
            c = zone_color(pct) if pct is not None else self.GREY
            parts = []
            if self.ui('pct'):
                parts.append('%s%d%%' % (ap, round(pct)) if pct is not None else '—')
            if extra:
                parts.append(extra)
            t = '  '.join(parts)
            return (c, [(t, c, False)] if (t and not only) else [], None, pct, 'lim')
        if self.ui('session'):
            lim.append(one(a.s_pct, (fmt_short(a.s_reset) if short else fmt_left(a.s_reset)) if (self.ui('time') and a.s_reset) else ''))
        if self.ui('week'):
            lim.append(one(a.w_pct, (datetime.fromtimestamp(a.w_reset).strftime('%d.%m') if short else fmt_date(a.w_reset)) if (self.ui('date') and a.w_reset) else ''))
        cache_on, tok_on = self.ui('cache'), self.ui('tokens')
        ci = self.chat_info(a) if (self.ui('chat') and (cache_on or tok_on)) else None
        chat = []
        if ci:
            tc = QColor(TOK_ZONES[ci['zone']])
            tokp = min(100.0, 100.0 * ci['used'] / ci['limit']) if tok_on else None
            if self.ui('chat_pos') == 'row':   # старый вид: чат третьей строкой
                chat.append((tc, [] if only else self.chat_segs(ci, cache_on, tok_on), ci, tokp, 'chat'))
            else:                              # чат отдельным блоком справа: сверху токены, снизу обратный отсчёт кэша
                if tok_on:
                    chat.append((tc, [] if only else self.chat_segs(ci, False, True), ci, tokp, 'tok'))
                if cache_on and ci['state']:
                    cs = [] if only else self.chat_segs(ci, True, False)
                    chat.append((cs[0][1] if cs else tc, cs, ci, None, 'cache'))
        if self.ui('chat_pos') == 'row':
            return [lim + chat]
        return [lim, chat] if chat else [lim]

    def beep(self, n=2):
        if not self.cfg.get('sound', True):
            return

        def run():
            try:
                import winsound
                for _ in range(n):
                    winsound.Beep(1000, 170); time.sleep(0.08)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def notify(self, title, msg):
        try:
            self.tray.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Warning, 8000)
        except Exception:
            pass

    def tick(self):
        """Раз в секунду: мигание, отсчёт кэша, сигналы."""
        if not any(a.running for a in self.st.values()):
            return
        self.update()
        now = time.time()
        for a in self.st.values():
            if not a.running:
                continue
            ci = self.chat_info(a)
            if not ci:
                continue
            if ci['state'] == 'beep':
                al = self._alarm.setdefault(a.key, {'n': 0, 'ack': False, 'last': 0.0})
                if a.al_ts != ci['ts']:
                    a.al_ts = ci['ts']; al.update(n=0, ack=False, last=0.0)   # новая серия сигналов для нового кэша
                    self.notify(a.title, 'Кэш скоро остынет — сделайте любой ход в чате.')
                if not al['ack'] and al['n'] < 10 and now - al['last'] >= 5:   # до 10 серий писка каждые 5 с, пока не кликнут по виджету
                    al['last'] = now; al['n'] += 1; self.beep()
            if ci['zone'] == 3 and a.al_zone != 3:
                self.notify(a.title, 'Чат заполнен: %s из %s. Переходите в новый чат.' % (fmt_k(ci['used']), fmt_k(ci['limit']))); self.beep(1)
            a.al_zone = ci['zone']

    def metrics(self):
        f, k = self.fonts(); fm = QFontMetrics(f)
        ic = int(self.cfg['icon'] * k) if self.ui('icon') else 0
        return f, k, fm, fm.height() + int(2 * k), ic, int(4 * k), int(6 * k), max(4, int(6 * k))

    def relayout(self):
        f, k, fm, lh, ic, pad, gap, dot = self.metrics()
        bars = self.ui('bars'); bpos = self.ui('bar_pos'); bw = max(10, int(self.ui('bar_w') * k)); dot_on = self.ui('dot')
        dgap = int(12 * k)   # промежуток с вертикальной перегородкой между блоком лимитов и блоком чата
        act = [self.st[x] for x in self.ORDER if self.st[x].running]
        norm = lambda t: re.sub(r'\d', '8', t)
        cells = []
        for a in act:
            cols = []
            for rows_in in self.cell_model(a):
                rows = []; tw_max = 0
                for dcol, segs, ci, pct, kind in rows_in:
                    has_bar = bool(bars and pct is not None)
                    if not segs and not has_bar:
                        continue
                    if segs and ci and ci['state'] and kind in ('chat', 'cache') and self.ui('cache'):   # место под самый длинный вариант «ХОД! 8:88»
                        tw = max(fm.horizontalAdvance('ХОД! 8:88'), fm.horizontalAdvance(norm(segs[0][0]))) + sum(fm.horizontalAdvance(norm(t)) for t, _, _ in segs[1:])
                    else:
                        tw = sum(fm.horizontalAdvance(norm(t)) for t, _, _ in segs)
                    if segs and dot_on:
                        tw += dot + int(4 * k)
                    rh = lh + (int(5 * k) if (has_bar and bpos == 'below') else 0)
                    rows.append((dcol, segs, ci, pct, tw, rh)); tw_max = max(tw_max, tw)
                cols.append({'rows': rows, 'tw': tw_max})
            cols = [c for c in cols if c['rows']] or cols[:1]
            cells.append({'a': a, 'cols': cols})
        shape = self.ui('shape')
        nmax = max([len(c['cols']) for c in cells] + [1])
        gtw = [max([c['cols'][j]['tw'] for c in cells if len(c['cols']) > j] + [0]) for j in range(nmax)]
        for c in cells:
            wsum = 0
            for j, col in enumerate(c['cols']):
                tw = gtw[j] if shape == 'stack' else col['tw']   # в столбике линии выровнены по одной колонке
                anyb = bool(bars and any(r[3] is not None for r in col['rows']))
                col['barx_off'] = 0
                if anyb and bpos == 'right':
                    col['barx_off'] = tw + int(6 * k); cw = col['barx_off'] + bw
                elif anyb and bpos == 'only':
                    cw = bw
                elif anyb and bpos == 'below':
                    cw = max(tw, int(30 * k))
                else:
                    cw = tw
                col['cw'] = cw; wsum += cw
            nrows = max([len(col['rows']) for col in c['cols']] + [0])
            c['rhs'] = [max([col['rows'][i][5] for col in c['cols'] if len(col['rows']) > i] + [lh]) for i in range(nrows)]
            c['dgap'] = dgap
            c['w'] = pad + ((ic + gap) if ic else 0) + wsum + (len(c['cols']) - 1) * dgap + pad + int(6 * k)   # запас справа: курсив и жирный шрифт шире расчёта
            c['h'] = max(ic, sum(c['rhs'])) + 2 * pad
        self.cells = cells; self.rows = []; self._seps = []
        if not cells:
            w, h = fm.horizontalAdvance('ИИ не запущен') + 4 * pad, lh + 2 * pad
        elif shape == 'strip':
            h = max(c['h'] for c in cells); x = 0
            for i, c in enumerate(cells):
                if i:
                    self._seps.append((x, pad, x, h - pad))
                c['rect'] = QRectF(x, 0, c['w'], h); x += c['w']
            w = x
        elif shape == 'grid':
            groups = [cells[i:i + 2] for i in range(0, len(cells), 2)]
            colw = [max([g[j]['w'] for g in groups if len(g) > j] + [0]) for j in range(2)]
            y = 0
            for gi, g in enumerate(groups):
                rh = max(c['h'] for c in g)
                if gi:
                    self._seps.append((pad, y, sum(colw[:len(groups[0])]) - pad, y))
                x = 0
                for j, c in enumerate(g):
                    c['rect'] = QRectF(x, y, colw[j], rh); x += colw[j]
                y += rh
            w, h = sum(colw[:min(2, len(cells))]), y
            if len(cells) > 1:
                self._seps.append((colw[0], pad, colw[0], h - pad))
        else:
            w = max(c['w'] for c in cells); y = 0
            for i, c in enumerate(cells):
                if i:
                    self._seps.append((pad, y, w - pad, y))
                c['rect'] = QRectF(0, y, w, c['h']); y += c['h']
            h = y
        self.rows = [(c['a'], c['rect']) for c in cells]
        self.setFixedSize(int(w), int(h)); self.update()
        for d in list(self._det.values()):   # открытые подробности обновляются вместе с данными (только если что-то изменилось)
            safe_call(d.build)
        if self._ready:   # окно видно, только пока запущен хоть один ИИ; без ИИ живёт в трее и появится само
            want = (bool(act) or self._force_show) and not self._user_hidden
            if want and not self.isVisible():
                self.show()
            elif not want and self.isVisible():
                self.hide()

    def draw_bar(self, p, bx, by, wd, pct, col, thk, th, bg):
        fl = wd * max(0.0, min(100.0, pct)) / 100.0
        sl = Qt.PenStyle.SolidLine; cap = Qt.PenCapStyle.RoundCap
        if bg == 'none':
            p.setPen(QPen(QColor(0, 0, 0, 200), thk + 2, sl, cap)); p.drawLine(QPointF(bx, by), QPointF(bx + wd, by))
        p.setPen(QPen(QColor(*th['track']), thk, sl, cap)); p.drawLine(QPointF(bx, by), QPointF(bx + wd, by))
        if fl >= 1:
            p.setPen(QPen(col, thk, sl, cap)); p.drawLine(QPointF(bx, by), QPointF(bx + fl, by))

    def paint_cell(self, p, c, f, k, fm, lh, ic, pad, gap, dot, th, blink_on, bg):
        a = c['a']; rc = c['rect']; x0 = rc.x() + pad
        adj = (lambda col: QColor(col).darker(135)) if th.get('darken') else (lambda col: QColor(col))
        if ic:
            if not a.icon.isNull():
                src = a.icon
                if th.get('darken') and a.key == 'codex':   # белый значок Codex на светлой теме: инвертируем в тёмный
                    if not hasattr(self, '_icon_inv'):
                        im = a.icon.toImage(); im.invertPixels(); self._icon_inv = QPixmap.fromImage(im)
                    src = self._icon_inv
                p.drawPixmap(int(x0), int(rc.y() + (rc.height() - ic) / 2), src.scaled(ic, ic, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            x0 += ic + gap
        bars = self.ui('bars'); bpos = self.ui('bar_pos'); bw = max(10, int(self.ui('bar_w') * k)); dot_on = self.ui('dot')
        rhs = c['rhs']; top = rc.y() + (rc.height() - sum(rhs)) / 2
        ys = [top + sum(rhs[:i]) for i in range(len(rhs))]
        cx = x0
        for j, col in enumerate(c['cols']):
            if j:   # вертикальная перегородка между блоком лимитов и блоком чата
                sx = cx - c['dgap'] / 2.0
                if bg != 'none':
                    p.setPen(QPen(QColor(*th['sep']), 1)); p.drawLine(int(sx), int(rc.y() + pad), int(sx), int(rc.y() + rc.height() - pad))
            for i, (dcol, segs, ci, pct, tw, rh) in enumerate(col['rows']):
                ry = ys[i]; tx = cx
                if segs and dot_on:
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(adj(dcol)); p.drawEllipse(QRectF(tx, ry + (lh - dot) / 2, dot, dot)); tx += dot + int(4 * k)
                base = ry + (lh + fm.ascent() - fm.descent()) / 2
                for txt, tcol, blk in segs:
                    cc = adj(tcol)
                    if blk and not blink_on:
                        cc.setAlpha(70)
                    if bg == 'none':   # без фона — тёмный контур, чтобы цифры читались на любых обоях
                        path = QPainterPath(); path.addText(tx, base, f, txt)
                        p.setPen(QPen(QColor(0, 0, 0, 220 if cc.alpha() > 100 else 60), max(2.0, 2.5 * k))); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
                        p.setPen(Qt.PenStyle.NoPen); p.setBrush(cc); p.drawPath(path)
                    else:
                        p.setPen(cc); p.drawText(int(tx), int(base), txt)
                    tx += fm.horizontalAdvance(txt)
                if bars and pct is not None:
                    thk = max(2.0, 2.0 * k); by = ry + lh / 2.0
                    if bpos == 'right':
                        bx, wd = cx + col['barx_off'], bw
                    elif bpos == 'below':
                        bx, wd, by = cx, col['cw'], ry + lh + int(2 * k) + thk / 2
                    else:
                        bx, wd = cx, bw
                    self.draw_bar(p, bx, by, wd, pct, adj(dcol), thk, th, bg)
            cx += col['cw'] + c['dgap']

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        f, k, fm, lh, ic, pad, gap, dot = self.metrics(); p.setFont(f)
        th = self.theme(); blink_on = int(time.time()) % 2 == 0
        bg = self.cfg['bg']
        if bg != 'none':
            al = 235 if bg == 'black' else int(255 * self.cfg['bg_alpha'] / 100)
            path = QPainterPath(); path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 5 * k, 5 * k)
            p.fillPath(path, QColor(th['bg'][0], th['bg'][1], th['bg'][2], al)); p.setPen(QPen(QColor(th['border'][0], th['border'][1], th['border'][2], al), 1)); p.drawPath(path)
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))   # почти невидимо, но ловит мышь
        cells = getattr(self, 'cells', [])
        if not cells:
            p.setPen(QColor(th['grey'])); p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, 'ИИ не запущен'); return
        if bg != 'none':
            p.setPen(QPen(QColor(*th['sep']), 1))
            for x1, y1, x2, y2 in self._seps:
                p.drawLine(int(x1), int(y1), int(x2), int(y2))
        for c in cells:
            self.paint_cell(p, c, f, k, fm, lh, ic, pad, gap, dot, th, blink_on, bg)

    def mousePressEvent(self, e):
        for al in self._alarm.values():   # клик по виджету гасит писк «ХОД!»
            al['ack'] = True
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft(); self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag is not None and not self.cfg.get('locked', False) and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag); self._moved = True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._dbl:   # второй «отпуск» после двойного клика — не открывать заново
                self._dbl = False; self._drag = None; return
            if self._moved:
                self.cfg['x'], self.cfg['y'] = self.x(), self.y(); save_cfg(self.cfg)
            else:
                pos = e.position()
                for a, rc in self.rows:
                    if rc.contains(pos):
                        self.open_details(a); break
            self._drag = None
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu().exec(e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):   # двойной клик по блоку: первый клик уже открыл подробности, второй ничего не меняет
        self._dbl = True

    def close_details(self):
        for d in list(self._det.values()):
            safe_call(d.close)
        self._det.clear()

    def open_details(self, a):
        same = a.key in self._det
        self.close_details()
        if same:   # повторный клик по той же строке — свернуть
            return
        d = Details(self, a); d.adjustSize(); self._det[a.key] = d
        if not self.cfg.get('details_pin', True):
            QTimer.singleShot(8000, lambda w=d: safe_call(w.close))
        g = QApplication.primaryScreen().availableGeometry()
        x = min(self.x(), g.right() - d.width()); y = self.y() - d.height() - 6
        if y < g.top():
            y = self.y() + self.height() + 6
        d.move(x, y); d.show()

    def menu(self):
        m = QMenu(self)
        m.addAction('Настройки…', lambda: Settings(self).exec())
        m.addAction('Обновить данные и токены чата', self.engine.refresh)
        p = m.addAction('Подробности: держать открытыми'); p.setCheckable(True); p.setChecked(self.cfg.get('details_pin', True))
        p.toggled.connect(lambda on: (self.cfg.__setitem__('details_pin', bool(on)), save_cfg(self.cfg)))
        m.addAction('Закрыть подробности', self.close_details)
        m.addAction('Конструктор вида…', lambda: Builder(self).exec())
        bz = m.addAction('Линии-индикаторы'); bz.setCheckable(True); bz.setChecked(self.ui('bars'))
        bz.toggled.connect(lambda on: self.set_ui(bars=bool(on)))
        m.addSeparator()
        wk = self.engine.workers.get('claude'); sess = getattr(self.engine, 'sessions', [])
        if wk and sess:   # какой аккаунт Claude показывать (как в старом виджете)
            sm = m.addMenu('Выбрать аккаунт Claude')
            sel = getattr(wk, 'selected_session_key', None)
            sm.addAction(('● ' if not sel else '') + 'Авто — как в Claude Desktop', lambda checked=False: wk.select_session(None))
            sm.addSeparator()
            for s in sess:
                sm.addAction(('● ' if s.get('session_key') == sel else '') + '%s (%s)' % (s.get('email') or 'Неизвестный', s.get('source_name') or 'Куки'),
                             lambda checked=False, k=s.get('session_key'): wk.select_session(k))
        am = m.addMenu('Аккаунты: подключить / переключить')
        try:
            cm = self.engine.mods.get('claude')
            profs = cm.load_cdp_profiles() if cm else []
        except Exception:
            profs = []
        for i, pr in enumerate(profs, 1):
            am.addAction('Claude: аккаунт %d — открыть вход в браузере' % i, lambda checked=False, p=pr: self.acc_claude_open(p))
        am.addAction('Claude: добавить ещё один аккаунт', self.acc_claude_add)
        am.addSeparator()
        am.addAction('Codex и Antigravity: как сменить аккаунт', self.acc_help)
        lk = m.addAction('Закрепить положение'); lk.setCheckable(True); lk.setChecked(self.cfg.get('locked', False))
        lk.toggled.connect(lambda on: (self.cfg.__setitem__('locked', bool(on)), save_cfg(self.cfg)))
        a = m.addAction('Запускать с Windows'); a.setCheckable(True); a.setChecked(self.autostart_on()); a.toggled.connect(self.set_autostart)
        m.addAction('Проверить обновление', lambda: self.check_update(True))
        m.addAction('Откатиться на другую версию…', self.rollback)
        au = m.addAction('Автообновление'); au.setCheckable(True); au.setChecked(not self.cfg.get('no_auto_update', False))
        au.toggled.connect(lambda on: (self.cfg.__setitem__('no_auto_update', not on), save_cfg(self.cfg)))
        m.addAction('Инструкция (ReadMe)', self.show_readme)
        m.addAction('Папка настроек и статистики', lambda: os.startfile(DATA))
        m.addSeparator()
        m.addAction('Версия ' + VERSION).setEnabled(False)
        m.addAction('Свернуть (вернётся сам при запуске ИИ)', self.minimize_to_tray)
        m.addAction('Закрыть полностью', self.quit_full)
        return m

    def acc_claude_open(self, pr):
        """Открыть видимое окно браузера виджета, чтобы войти в нужный аккаунт claude.ai."""
        try:
            self.engine.mods['claude'].launch_cdp_chrome(pr['port'], pr['profile'], visible=True)
            self.notify('AIWidgetDock', 'Войдите в claude.ai в открывшемся окне. Потом закройте его: виджет подхватит аккаунт сам.')
        except Exception as ex:
            log('acc open: %r' % ex); self.notify('AIWidgetDock', 'Не удалось открыть браузер: %s' % ex)

    def acc_claude_add(self):
        """Добавить ещё один браузер виджета (отдельный профиль) для второго аккаунта Claude."""
        try:
            cm = self.engine.mods['claude']
            lst = cm.load_cdp_profiles()
            port = max([x['port'] for x in lst] + [9230]) + 1
            while port in (9223, 9224, 9225, 9226):   # эти порты занимают чужие браузеры
                port += 1
            prof = 'ClaudeWidget%d' % (len(lst) + 1)
            while any(x['profile'] == prof for x in lst):
                prof += 'x'
            lst.append({'port': port, 'profile': prof}); cm.save_cdp_profiles(lst)
            cm.launch_cdp_chrome(port, prof, visible=True)
            self.notify('AIWidgetDock', 'Новый аккаунт: войдите в claude.ai в открывшемся окне, потом закройте его.')
            QTimer.singleShot(20000, self.engine.refresh)
        except Exception as ex:
            log('acc add: %r' % ex); self.notify('AIWidgetDock', 'Не удалось добавить аккаунт: %s' % ex)

    def acc_help(self):
        QMessageBox.information(self, APP,
            'Виджет читает тот аккаунт, в который вошла сама программа.\n\n'
            'Codex: выйдите в приложении Codex и войдите другим аккаунтом, затем сделайте один запрос. '
            'Виджет подхватит новые лимиты сам.\n\n'
            'Antigravity: войдите в нужный аккаунт Google в самой программе. '
            'Если данных нет, закройте и откройте Antigravity.\n\n'
            'Claude: пункты выше в этом меню открывают отдельное окно браузера для входа в claude.ai.')

    def hide_bridges(self):
        try:
            m = self.engine.mods.get('claude')
            ports = {int(p['port']) for p in m.load_cdp_profiles()} if m else {9222, 9230}
            hide_bridge_windows(ports)
        except Exception as ex:
            log('hide bridges: %r' % ex)

    def minimize_to_tray(self):
        self._user_hidden = True; self._force_show = False; self.hide()
        self.notify('AIWidgetDock', 'Виджет свёрнут в трей. Появится сам, когда запустится ИИ, или по клику на значок в трее.')

    def on_tray(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._user_hidden = False; self._force_show = True; self.relayout()   # показать даже без запущенных ИИ

    def quit_full(self):
        if QMessageBox.question(self, APP, 'Закрыть виджет полностью?\nОн снова запустится при входе в Windows или из меню «Пуск».') == QMessageBox.StandardButton.Yes:
            QApplication.quit()

    def show_readme(self):
        d = QDialog(self); d.setWindowTitle('AIWidgetDock — инструкция'); d.resize(560, 560)
        v = QVBoxLayout(d); t = QTextBrowser(); t.setHtml(README); v.addWidget(t)
        b = QPushButton('Понятно'); b.clicked.connect(d.accept); v.addWidget(b); d.exec()

    RUNKEY = r'Software\Microsoft\Windows\CurrentVersion\Run'

    def autostart_on(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUNKEY) as k:
                winreg.QueryValueEx(k, APP); return True
        except Exception:
            return False

    def set_autostart(self, on):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUNKEY, 0, winreg.KEY_SET_VALUE) as k:
                if on:
                    winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, '"%s"' % sys.executable)
                else:
                    winreg.DeleteValue(k, APP)
        except Exception as e:
            log('autostart: %r' % e)

    def self_heal(self):
        """Автозапуск и ярлык в «Пуске» всегда ведут на тот exe, который реально запущен (после переноса, отката, обновления)."""
        if not getattr(sys, 'frozen', False):
            return
        try:
            import winreg
            cur = None
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUNKEY) as k:
                    cur = winreg.QueryValueEx(k, APP)[0]
            except Exception:
                pass
            if cur is not None and cur != '"%s"' % sys.executable:
                self.set_autostart(True); log('autostart path fixed: %s -> %s' % (cur, sys.executable))
            lnk = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'AIWidgetDock.lnk')
            if os.path.exists(lnk):
                ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');if($s.TargetPath -ne '%s'){$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.Save()}"
                      % (lnk, sys.executable, sys.executable, os.path.dirname(sys.executable)))
                subprocess.run(['powershell', '-NoProfile', '-Command', ps], creationflags=0x08000000, timeout=30)
        except Exception as ex:
            log('self heal: %r' % ex)

    def check_failed_update(self):
        """Если прошлая установка не запустилась и bat вернул прежнюю версию, сообщить об этом."""
        f = os.path.join(DATA, 'update_failed.txt')
        try:
            if not os.path.exists(f):
                return
            tag = open(f, encoding='utf-8').read().strip() or '?'
            os.remove(f)
        except Exception:
            return
        rec = ''
        try:   # рекомендуем последнюю выпущенную версию
            req = urllib.request.Request('https://api.github.com/repos/%s/releases/latest' % REPO, headers={'User-Agent': APP})
            lt = str(json.load(urllib.request.urlopen(req, timeout=5)).get('tag_name', '')).lstrip('vV')
            if lt and lt != tag:
                rec = '\nРекомендуем попробовать последнюю версию: %s.' % lt
        except Exception:
            pass
        if QMessageBox.question(self, APP, 'Версия %s не запустилась, поэтому возвращена версия %s. Всё работает как раньше.%s\n\n'
                                'Открыть список версий, чтобы выбрать другую?' % (tag, VERSION, rec)) == QMessageBox.StandardButton.Yes:
            self.rollback()

    def _install_exe(self, url, tag=''):
        """Скачать exe и заменить запущенный файл безопасно: прежняя версия сохраняется в AppData\\AIWidgetDock\\prev."""
        new = os.path.join(tempfile.gettempdir(), 'AIWidgetDock_new.exe')
        urllib.request.urlretrieve(url, new)
        exe = sys.executable
        prevdir = os.path.join(DATA, 'prev'); os.makedirs(prevdir, exist_ok=True)
        prev = os.path.join(prevdir, 'AIWidgetDock_prev.exe')
        shutil.copy2(exe, prev)
        flag = os.path.join(DATA, 'started.flag'); target = os.path.join(DATA, 'update_target.txt'); failed = os.path.join(DATA, 'update_failed.txt')
        with open(target, 'w', encoding='utf-8') as f:
            f.write(tag or '?')
        ver = lambda x: tuple(int(p) for p in x.split('.') if p.isdigit())
        text = update_bat_text(new, exe, flag, target, os.path.basename(exe), prev, failed, bool(tag) and ver(tag) >= (1, 3, 0))
        bat = os.path.join(tempfile.gettempdir(), 'aiwd_update.bat')
        with open(bat, 'w', encoding='utf-8') as f:
            f.write(text)
        # новый exe не должен наследовать переменные PyInstaller старого (иначе «Failed to load Python DLL»)
        env = {k: v for k, v in os.environ.items() if not (k.startswith('_PYI') or k == '_MEIPASS2')}
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        subprocess.Popen(['cmd', '/c', bat], creationflags=0x08000000, env=env); QApplication.quit()

    def rollback(self):
        """Установить любую из прошлых версий из GitHub Releases."""
        if not getattr(sys, 'frozen', False):
            QMessageBox.information(self, APP, 'Откат работает только в собранной программе (exe).'); return
        try:
            req = urllib.request.Request('https://api.github.com/repos/%s/releases?per_page=40' % REPO, headers={'User-Agent': APP})
            rels = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception as e:
            QMessageBox.warning(self, APP, 'Не удалось получить список версий: %s' % e); return
        items = []
        for r in rels:
            if r.get('draft') or r.get('prerelease'):
                continue
            asset = next((x for x in r.get('assets', []) if x.get('name', '').lower().endswith('.exe')), None)
            if asset:
                d = (r.get('published_at') or '')[:10]
                items.append((str(r.get('tag_name', '')).lstrip('vV'), (d[8:10] + '.' + d[5:7] + '.' + d[0:4]) if d else '', r, asset))
        if not items:
            QMessageBox.information(self, APP, 'В релизах нет доступных версий.'); return
        labels = ['%s  (%s)%s' % (t, d, '  — у вас сейчас' if t == VERSION else '') for t, d, _, _ in items]
        lab, ok = QInputDialog.getItem(self, APP, 'Версия, на которую перейти:', labels, 0, False)
        if not ok:
            return
        t, d, r, asset = items[labels.index(lab)]
        if t == VERSION:
            QMessageBox.information(self, APP, 'Эта версия уже установлена.'); return
        notes = (r.get('body') or '').strip()[:600] or '—'
        ver = lambda x: tuple(int(p) for p in x.split('.') if p.isdigit())
        extra = ('\n\nВнимание: версии до 1.2.1 сами обновляются вперёд с ошибкой «Failed to load Python DLL». Если после такого обновления виджет пропал, '
                 'просто запустите его заново ярлыком AIWidgetDock из меню «Пуск». Если версия не запустится сразу, прежняя вернётся сама.') if ver(t) < (1, 2, 1) else ''
        if QMessageBox.question(self, APP, 'Установить версию %s?\n\nЧто в ней:\n%s\n\nВиджет перезапустится. Настройки сохранятся. Автообновление при этом выключится, чтобы виджет не вернулся на новую версию сам (включается в меню «Автообновление»).%s' % (t, notes, extra)) != QMessageBox.StandardButton.Yes:
            return
        self.cfg['no_auto_update'] = (t != items[0][0]); save_cfg(self.cfg)
        try:
            self._install_exe(asset['browser_download_url'], t)
        except Exception as e:
            log('rollback: %r' % e); QMessageBox.warning(self, APP, 'Не удалось установить версию: %s' % e)

    def check_update(self, manual):
        """Обновление с GitHub Releases: новая версия с номером и «что нового»."""
        if not manual and self.cfg.get('no_auto_update'):
            return
        try:
            req = urllib.request.Request('https://api.github.com/repos/%s/releases/latest' % REPO, headers={'User-Agent': APP})
            r = json.load(urllib.request.urlopen(req, timeout=10))
            tag = str(r.get('tag_name', '')).lstrip('vV')
            ver = lambda s: tuple(int(x) for x in s.split('.') if x.isdigit())
            if not manual and getattr(self, '_declined', None) == tag:
                return
            if not tag or ver(tag) <= ver(VERSION):
                if manual:
                    QMessageBox.information(self, APP, 'У вас последняя версия %s.' % VERSION)
                return
            notes = (r.get('body') or '').strip() or '—'
            msg = ('Доступна новая версия %s (у вас %s).\n\nЧто нового:\n%s\n• улучшена производительность\n• улучшена обработка данных\n\nОбновить сейчас?' % (tag, VERSION, notes))
            if QMessageBox.question(self, APP, msg) != QMessageBox.StandardButton.Yes:
                self._declined = tag
                return
            asset = next((x for x in r.get('assets', []) if x.get('name', '').lower().endswith('.exe')), None)
            if not asset or not getattr(sys, 'frozen', False):
                QMessageBox.information(self, APP, 'Скачайте новую версию со страницы:\n' + r.get('html_url', '')); return
            self.cfg['no_auto_update'] = False; save_cfg(self.cfg)
            self._install_exe(asset['browser_download_url'], tag)
        except Exception as e:
            log('update: %r' % e)
            if manual:
                QMessageBox.warning(self, APP, 'Не удалось проверить обновление: %s' % e)


_CRASH_FILE = None
_EX_COUNT = [0]


def safe_call(fn, *args):
    """Вызов, который не роняет программу, если окно уже закрыто и удалено (RuntimeError: wrapped C/C++ object has been deleted)."""
    try:
        fn(*args)
    except RuntimeError:
        pass
    except Exception as ex:
        log('safe_call: %r' % ex)


def install_crash_guards():
    """PyQt6 при необработанной ошибке Python в обработчике событий вызывает qFatal() и закрывает программу
    (в Windows это «ошибка в Qt6Core.dll», код 0xc0000409). Свой excepthook отменяет это: ошибка пишется в журнал,
    программа продолжает работать. faulthandler пишет стек в crash.log, если авария всё же случилась."""
    import traceback
    import faulthandler

    def hook(tp, ex, tb):
        try:
            _EX_COUNT[0] += 1
            if _EX_COUNT[0] <= 200:   # чтобы повторяющаяся ошибка не раздувала журнал
                log('UNHANDLED ' + ''.join(traceback.format_exception(tp, ex, tb))[-1800:])
        except Exception:
            pass
    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    try:
        global _CRASH_FILE
        _CRASH_FILE = open(os.path.join(DATA, 'crash.log'), 'a', encoding='utf-8')
        faulthandler.enable(_CRASH_FILE)
    except Exception:
        pass


def main():
    install_crash_guards()
    app = QApplication(sys.argv); app.setQuitOnLastWindowClosed(False)
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, 'Global\\AIWidgetDock')
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    d = Dock(); d._ready = True; d.relayout()
    try:   # отметка «запустился»: по ней установщик понимает, что новая версия работает
        with open(os.path.join(DATA, 'started.flag'), 'w', encoding='utf-8') as f:
            f.write(VERSION)
    except Exception as ex:
        log('started flag: %r' % ex)
    QTimer.singleShot(3000, d.check_failed_update)
    threading.Thread(target=d.self_heal, daemon=True).start()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
