# -*- coding: utf-8 -*-
"""AIWidgetDock — компактный виджет лимитов ИИ (Claude, Codex, Antigravity, Grok).
Один блок: строка ИИ появляется, только пока его программа запущена.
Движок чтения лимитов — тот же, что у виджетов DesktopClaudeWidget (engine.py)."""
import sys, os, re, json, time, glob, csv, threading, subprocess, importlib.util, urllib.request, tempfile
from datetime import datetime

APP = 'AIWidgetDock'
VERSION = '1.1.1'
REPO = 'ivankoval921-bit/AIWidgetDock'
DATA = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), APP)
os.makedirs(os.path.join(DATA, 'engine'), exist_ok=True)
RES = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
CFG_FILE = os.path.join(DATA, 'settings.json')
STATS_FILE = os.path.join(DATA, 'stats.csv')
LOG_FILE = os.path.join(DATA, 'dock.log')

from PyQt6.QtCore import Qt, QTimer, QRectF, QObject, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap, QPen, QPainterPath, QFontMetrics, QIcon
from PyQt6.QtWidgets import (QApplication, QWidget, QMenu, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QSlider, QCheckBox, QPushButton, QFormLayout, QMessageBox, QTextBrowser, QSystemTrayIcon, QSpinBox)


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
            'ttl': {'claude': 'auto', 'codex': 'auto'}}
TTLS = [('Авто', 'auto'), ('5 минут', '300'), ('10 минут', '600'), ('1 час', '3600')]
FONTS = ['Segoe UI', 'Segoe UI Semibold', 'Arial', 'Verdana', 'Tahoma', 'Calibri', 'Consolas', 'Bahnschrift',
         'Trebuchet MS', 'Roboto', 'Cascadia Mono', 'Georgia']


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
ZONES = ['#2fb35a', '#3b82d6', '#e0b400', '#f07a16', '#e8203c']


def zone_color(pct):
    try:
        p = float(pct)
    except Exception:
        return QColor('#9aa0a6')
    return QColor(ZONES[min(4, max(0, int(p // 20)))])


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


# ---------- данные ----------
class AIState:
    def __init__(self, key, title, icon):
        self.key, self.title, self.icon = key, title, icon
        self.running = False
        self.s_pct = None; self.s_reset = 0.0
        self.w_pct = None; self.w_reset = 0.0
        self.ctx_used = None; self.ctx_max = None
        self.approx = False
        self.updated = 0.0
        self.source = ''
        self.ch_used = None; self.ch_ts = 0.0; self.ch_ttl = 0; self.ch_src = ''   # текущий чат: токены, время последнего хода
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
            a.ch_used, a.ch_ts, a.ch_src = used, ts, os.path.basename(best)
            if ttl:
                a.ch_ttl = ttl
        except Exception as e:
            log('claude chat: %r' % e)
        self.bus.changed.emit()

    def on_ctx(self, key, d):
        a = self.st[key]
        if not d or d.get('stale') or not d.get('tokens'):
            a.ctx_used = a.ctx_max = None
        else:
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
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet('QWidget{background:#0d0f12;color:#ddd;font:12px "Segoe UI"} QPushButton{background:#1b1e23;border:1px solid #444;border-radius:4px;padding:3px 8px}')
        v = QVBoxLayout(self); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(4)
        v.addWidget(QLabel('<b>%s</b>' % a.title))

        def bar(name, pct, extra, col=None, txt=None):
            if pct is None:
                return
            c = col or zone_color(pct).name()
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
                TOK_ZONES[ci['zone']], '%d тыс.' % (ci['used'] // 1000))
            if ci['state'] == 'dead':
                v.addWidget(QLabel('Кэш: <b style="color:#8a8f98">остыл</b> — следующий ход заново читает весь чат, это дороже'))
            elif ci['state']:
                left = ci['left']
                bar('Кэш', 100.0 * left / ci['ttl'], 'Осталось %d мин из %d. Любой ход в чате обновляет кэш.' % (round(left / 60), round(ci['ttl'] / 60)),
                    '#2fb35a' if ci['state'] == 'ok' else '#e8203c', '%d мин' % max(1, round(left / 60)))
        if a.s_pct is None and a.w_pct is None:
            v.addWidget(QLabel('Нет данных. Правый клик → «Инструкция»: как подключить аккаунт.'))
        h = QHBoxLayout()
        b1 = QPushButton('Обновить'); b1.clicked.connect(lambda: (dock.engine.refresh(), self.close()))
        b2 = QPushButton('Инструкция'); b2.clicked.connect(lambda: (self.close(), dock.show_readme()))
        h.addWidget(b1); h.addWidget(b2); v.addLayout(h)
        if a.updated:
            u = QLabel('Обновлено %s · %s' % (datetime.fromtimestamp(a.updated).strftime('%H:%M:%S'), a.source)); u.setStyleSheet('color:#666;font-size:10px'); v.addWidget(u)


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
Цвет: зелёный 0–20%, синий 20–40%, жёлтый 40–60%, оранжевый 60–80%, красный 80–100%.</p>
<p>Строка ИИ появляется, когда его программа запущена, и пропадает, когда она закрыта.</p>
<h4>Установка</h4><ol><li>Скопируйте <b>AIWidgetDock.exe</b> в любую папку (например D:\\Programs) и запустите.</li>
<li>Чтобы запускался вместе с Windows: правый клик по виджету → «Запускать с Windows».</li></ol>
<h4>Строка «чат» (новое в 1.1)</h4>
<p>Третья строка показывает <b>текущий чат</b>: «кэш 54м  187k/400k».
<b>Кэш</b> — сколько ещё минут ИИ читает ваш чат из дешёвой памяти. Каждый ход обновляет отсчёт. Когда остаётся меньше четверти срока, цифра становится красной и мигает,
за последние минуты появляется «ХОД!» и звуковой сигнал: сделайте любой ход в чате (хоть «подожди, думаю»). Если кэш остыл, следующий ход дороже.
<b>Токены</b> — заполнение окна чата: зелёный до 150k, жёлтый до 300k, оранжевый до 350k, красный выше (для предела 400k; доли те же при другом пределе).
В красной зоне цифра мигает: пора переходить в новый чат. Пределы и срок кэша задаются в настройках.
Claude Code и Codex считаются автоматически из журналов; у остальных токены обновляются кнопкой «Обновить», а кэш-таймера нет.</p>
<h4>Подключение аккаунтов</h4><ul>
<li><b>Claude</b> — откройте Claude Desktop и войдите в аккаунт (или войдите на claude.ai в Chrome / Firefox). Виджет найдёт вход сам.</li>
<li><b>Codex</b> — войдите в приложение Codex и сделайте хотя бы один запрос: Codex сам записывает лимиты, виджет их читает.</li>
<li><b>Antigravity</b> — откройте Antigravity и войдите в аккаунт Google.</li>
<li><b>Grok</b> — точных лимитов у Grok нет, показывается примерный расход со знаком «~».</li></ul>
<h4>Управление</h4><ul><li>Перетащить — левой кнопкой за блок. Клик по строке — подробности: сессия, неделя, окно контекста (где программа его отдаёт).</li>
<li>Правый клик — меню: Настройки (шрифт, жирность, курсив, размер, масштаб, фон: чёрный / полупрозрачный / без фона), Обновить, Проверить обновление, Инструкция, Выход.</li></ul>
<p>Настройки и статистика: %APPDATA%\\AIWidgetDock (stats.csv — журнал цифр для проверки расчётов).</p>"""


# ---------- главный блок ----------
class Dock(QWidget):
    ORDER = ['claude', 'codex', 'antigravity', 'grok']

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.cfg = load_cfg()
        self.bus = Bus(); self.bus.changed.connect(self.relayout)
        icons = {k: os.path.join(RES, 'assets', 'icon_%s.png' % k) for k in self.ORDER}
        self.st = {k: AIState(k, t, QPixmap(icons[k]) if os.path.exists(icons[k]) else QPixmap())
                   for k, t in (('claude', 'Claude'), ('codex', 'Codex'), ('antigravity', 'Antigravity'), ('grok', 'Grok'))}
        self.engine = Engine(self.st, self.bus)
        self.rows = []; self._drag = None; self._moved = False; self._procs = {}
        self.t_proc = QTimer(self); self.t_proc.timeout.connect(self.check_procs); self.t_proc.start(5000)
        self.t_codex = QTimer(self); self.t_codex.timeout.connect(self.engine.read_all); self.t_codex.start(20000)
        self.t_tick = QTimer(self); self.t_tick.timeout.connect(self.relayout); self.t_tick.start(30000)
        self.t_blink = QTimer(self); self.t_blink.timeout.connect(self.tick); self.t_blink.start(1000)
        self.t_stat = QTimer(self); self.t_stat.timeout.connect(lambda: [stat(a, 'hourly') for a in self.st.values() if a.running]); self.t_stat.start(3600000)
        self.tray = QSystemTrayIcon(QIcon(icons['claude']), self); self.tray.setToolTip('AIWidgetDock'); self.tray.setContextMenu(self.menu()); self.tray.show()
        self.check_procs(); self.engine.read_all(); self.relayout()
        if self.cfg['x'] >= 0:
            self.move(self.cfg['x'], self.cfg['y'])
        else:
            g = QApplication.primaryScreen().availableGeometry(); self.move(g.right() - self.width() - 20, g.bottom() - self.height() - 70)
        if self.cfg.get('first_run'):
            self.cfg['first_run'] = False; save_cfg(self.cfg); QTimer.singleShot(800, self.show_readme)
        QTimer.singleShot(15000, lambda: self.check_update(False))
        self.t_upd = QTimer(self); self.t_upd.timeout.connect(lambda: self.check_update(False)); self.t_upd.start(6 * 3600 * 1000)   # и дальше раз в 6 часов

    def check_procs(self):
        """Строка ИИ видна, только пока его программа работает."""
        def run():
            try:
                out = subprocess.check_output(['tasklist', '/FO', 'CSV', '/NH'], creationflags=0x08000000).decode('utf-8', 'ignore').lower()
            except Exception:
                out = ''
            r = {'claude': proc_running(['claude.exe'], out), 'codex': proc_running(['codex.exe', 'chatgpt.exe', 'openai.codex.exe'], out),
                 'antigravity': proc_running(['antigravity.exe'], out), 'grok': proc_running(['grok.exe'], out)}
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
            used, ts = a.ch_used, a.ch_ts
        elif a.key != 'codex' and a.ctx_used:
            used, ts = a.ctx_used, 0.0
        else:
            return None
        limit = max(1, int(self.cfg.get('chat_limit', {}).get(a.key, 400))) * 1000
        r = used / float(limit)
        ci = {'used': used, 'limit': limit, 'zone': 0 if r < 0.375 else 1 if r < 0.75 else 2 if r < 0.875 else 3,
              'ts': ts, 'ttl': 0, 'left': None, 'state': None}
        if ts:
            ttl = self.ttl_for(a); left = ts + ttl - now
            ci.update(ttl=ttl, left=left, state='dead' if left <= 0 else 'beep' if left <= min(300, ttl * 0.1) else 'warn' if left <= ttl * 0.25 else 'ok')
        return ci

    def chat_segs(self, ci):
        tc = QColor(TOK_ZONES[ci['zone']]); segs = []
        st = ci['state']
        if st == 'dead':
            segs.append(('кэш остыл', QColor('#8a8f98'), False)); segs.append(('  ', tc, False))
        elif st:
            left = ci['left']
            t = ('%dм' % -(-int(left) // 60)) if left >= 600 else '%d:%02d' % (int(left) // 60, int(left) % 60)
            if st == 'beep':
                segs.append(('ХОД! ' + t, QColor('#e8203c'), True))
            elif st == 'warn':
                segs.append(('кэш ' + t, QColor('#e8203c'), True))
            else:
                segs.append(('кэш ' + t, QColor('#2fb35a'), False))
            segs.append(('  ', tc, False))
        segs.append(('%s/%s%s' % (fmt_k(ci['used']), fmt_k(ci['limit']), '!' if ci['zone'] == 3 else ''), tc, ci['zone'] == 3))
        return segs

    def lines(self, a):
        """[(цвет точки, [(текст, цвет, мигает)], инфо чата)]"""
        ap = '~' if a.approx else ''

        def one(pct, sub):
            t = ('%s%d%%' % (ap, round(pct)) if pct is not None else '—') + ('  ' + sub if sub else '')
            c = zone_color(pct) if pct is not None else self.GREY
            return (c, [(t, c, False)], None)
        L = [one(a.s_pct, fmt_left(a.s_reset) if a.s_reset else ''), one(a.w_pct, fmt_date(a.w_reset))]
        ci = self.chat_info(a)
        if ci:
            L.append((QColor(TOK_ZONES[ci['zone']]), self.chat_segs(ci), ci))
        return L

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
                if a.al_ts != ci['ts']:
                    a.al_ts = ci['ts']; a.al_beep = 0.0
                    self.notify(a.title, 'Кэш скоро остынет — сделайте любой ход в чате.')
                if now - a.al_beep >= 60:
                    a.al_beep = now; self.beep()
            if ci['zone'] == 3 and a.al_zone != 3:
                self.notify(a.title, 'Чат заполнен: %s из %s. Переходите в новый чат.' % (fmt_k(ci['used']), fmt_k(ci['limit']))); self.beep(1)
            a.al_zone = ci['zone']

    def metrics(self):
        f, k = self.fonts(); fm = QFontMetrics(f)
        return f, k, fm, fm.height() + int(2 * k), int(self.cfg['icon'] * k), int(4 * k), int(6 * k), max(4, int(6 * k))

    def relayout(self):
        f, k, fm, lh, ic, pad, gap, dot = self.metrics()
        self.rows = []; y = pad; w = 0
        act = [self.st[x] for x in self.ORDER if self.st[x].running]
        norm = lambda t: re.sub(r'\d', '8', t)
        for i, a in enumerate(act):
            if i:
                y += gap
            L = self.lines(a)
            h = max(ic, len(L) * lh)
            txtw = 0
            for _, segs, ci in L:
                if ci and ci['state']:   # место под самый длинный вариант «ХОД! 8:88»
                    tw = fm.horizontalAdvance('ХОД! 8:88') + sum(fm.horizontalAdvance(norm(t)) for t, _, _ in segs[1:])
                else:
                    tw = sum(fm.horizontalAdvance(norm(t)) for t, _, _ in segs)
                txtw = max(txtw, tw)
            self.rows.append((a, y, h)); w = max(w, pad + ic + gap + dot + int(4 * k) + txtw + pad + 2)
            y += h
        if not act:
            w, y = fm.horizontalAdvance('ИИ не запущен') + 4 * pad, lh + pad
        self.setFixedSize(int(w), int(y + pad)); self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        f, k, fm, lh, ic, pad, gap, dot = self.metrics(); p.setFont(f)
        blink_on = int(time.time()) % 2 == 0
        bg = self.cfg['bg']
        if bg != 'none':
            al = 235 if bg == 'black' else int(255 * self.cfg['bg_alpha'] / 100)
            path = QPainterPath(); path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 5 * k, 5 * k)
            p.fillPath(path, QColor(13, 15, 18, al)); p.setPen(QPen(QColor(42, 45, 51, al), 1)); p.drawPath(path)
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))   # почти невидимо, но ловит мышь
        if not self.rows:
            p.setPen(QColor('#888')); p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, 'ИИ не запущен'); return
        for i, (a, y, h) in enumerate(self.rows):
            if i and bg != 'none':
                p.setPen(QPen(QColor(232, 232, 232, 190), 1)); sy = int(y - gap / 2); p.drawLine(pad, sy, self.width() - pad, sy)
            if not a.icon.isNull():
                p.drawPixmap(pad, int(y + (h - ic) / 2), a.icon.scaled(ic, ic, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            x0 = pad + ic + gap
            L = self.lines(a); n = len(L)
            for j, (dcol, segs, _) in enumerate(L):
                ly = y + (h - n * lh) / 2 + j * lh
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(dcol); p.drawEllipse(QRectF(x0, ly + (lh - dot) / 2, dot, dot))
                tx = x0 + dot + int(4 * k); base = ly + (lh + fm.ascent() - fm.descent()) / 2
                for txt, col, blk in segs:
                    c = QColor(col)
                    if blk and not blink_on:
                        c.setAlpha(70)
                    if bg == 'none':   # без фона — тёмный контур, чтобы цифры читались на любых обоях
                        path = QPainterPath(); path.addText(tx, base, f, txt)
                        p.setPen(QPen(QColor(0, 0, 0, 220 if c.alpha() > 100 else 60), max(2.0, 2.5 * k))); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
                        p.setPen(Qt.PenStyle.NoPen); p.setBrush(c); p.drawPath(path)
                    else:
                        p.setPen(c); p.drawText(int(tx), int(base), txt)
                    tx += fm.horizontalAdvance(txt)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft(); self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag); self._moved = True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._moved:
                self.cfg['x'], self.cfg['y'] = self.x(), self.y(); save_cfg(self.cfg)
            else:
                yy = e.position().y()
                for a, y, h in self.rows:
                    if y - 3 <= yy <= y + h + 3:
                        self.open_details(a); break
            self._drag = None
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu().exec(e.globalPosition().toPoint())

    def open_details(self, a):
        d = Details(self, a); d.adjustSize()
        g = QApplication.primaryScreen().availableGeometry()
        x = min(self.x(), g.right() - d.width()); y = self.y() - d.height() - 6
        if y < g.top():
            y = self.y() + self.height() + 6
        d.move(x, y); d.show()

    def menu(self):
        m = QMenu(self)
        m.addAction('Настройки…', lambda: Settings(self).exec())
        m.addAction('Обновить данные и токены чата', self.engine.refresh)
        m.addSeparator()
        a = m.addAction('Запускать с Windows'); a.setCheckable(True); a.setChecked(self.autostart_on()); a.toggled.connect(self.set_autostart)
        m.addAction('Проверить обновление', lambda: self.check_update(True))
        m.addAction('Инструкция (ReadMe)', self.show_readme)
        m.addAction('Папка настроек и статистики', lambda: os.startfile(DATA))
        m.addSeparator()
        m.addAction('Версия ' + VERSION).setEnabled(False)
        m.addAction('Выход', QApplication.quit)
        return m

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

    def check_update(self, manual):
        """Обновление с GitHub Releases: новая версия с номером и «что нового»."""
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
            new = os.path.join(tempfile.gettempdir(), 'AIWidgetDock_new.exe')
            urllib.request.urlretrieve(asset['browser_download_url'], new)
            bat = os.path.join(tempfile.gettempdir(), 'aiwd_update.bat')
            with open(bat, 'w', encoding='utf-8') as f:
                f.write('@echo off\r\nchcp 65001 >nul\r\nset n=0\r\n:r\r\nset /a n+=1\r\nping 127.0.0.1 -n 3 >nul\r\ncopy /y "%s" "%s" >nul 2>&1\r\nif errorlevel 1 if %%n%% LSS 20 goto r\r\nstart "" "%s"\r\n' % (new, sys.executable, sys.executable))
            subprocess.Popen(['cmd', '/c', bat], creationflags=0x08000000); QApplication.quit()
        except Exception as e:
            log('update: %r' % e)
            if manual:
                QMessageBox.warning(self, APP, 'Не удалось проверить обновление: %s' % e)


def main():
    app = QApplication(sys.argv); app.setQuitOnLastWindowClosed(False)
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, 'Global\\AIWidgetDock')
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    d = Dock(); d.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
