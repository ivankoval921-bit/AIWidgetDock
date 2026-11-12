"""Контекст и вход/выход токенов последнего чата Claude (Cowork и Claude Code) по локальным журналам."""
import glob
import json
import os
import time

APPDATA = os.environ.get('APPDATA', '')
HOME = os.path.expanduser('~')
COWORK_ROOT = os.path.join(APPDATA, 'Claude', 'local-agent-mode-sessions')
CODE_ROOT = os.path.join(HOME, '.claude', 'projects')
_windows = {}  # model -> contextWindow (запоминается из журналов Cowork)


def _cowork_title(audit):
    try:
        with open(os.path.dirname(audit) + '.json', encoding='utf-8') as f:
            d = json.load(f)
        return d.get('title') or d.get('sessionId')
    except (OSError, ValueError):
        return None


def list_tasks():
    tasks = []
    for path in glob.glob(os.path.join(COWORK_ROOT, '*', '*', 'local_*.json')):
        try:
            with open(path, encoding='utf-8') as stream:
                data = json.load(stream)
            tasks.append({'path': path, 'title': 'Cowork: ' + str(data.get('title') or data.get('sessionId')),
                          'modified': os.path.getmtime(path)})
        except (OSError, ValueError):
            continue
    for path in glob.glob(os.path.join(CODE_ROOT, '*', '*.jsonl')):
        try:
            tasks.append({'path': path, 'title': 'Code: ' + os.path.basename(path)[:8],
                          'modified': os.path.getmtime(path)})
        except OSError:
            continue
    return sorted(tasks, key=lambda item: item['modified'], reverse=True)[:25]


def latest_path():
    """Самый свежий журнал чата — неважно, на каком диске лежит проект."""
    cands = glob.glob(os.path.join(COWORK_ROOT, '*', '*', 'local_*', 'audit.jsonl'))
    cands += glob.glob(os.path.join(CODE_ROOT, '*', '*.jsonl'))
    best = None
    for p in cands:
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if best is None or m > best[0]:
            best = (m, p)
    return best[1] if best else None


def _resolve(path):
    if path and path.endswith('.json') and not path.endswith('.jsonl'):
        return path[:-5] + os.sep + 'audit.jsonl'  # старый формат: путь к local_*.json
    return path


def read_context(path):
    path = _resolve(path)
    if not path or not os.path.isfile(path):
        return {}
    latest, title, outs = None, None, {}
    try:
        with open(path, encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('type') == 'ai-title':
                    title = row.get('aiTitle') or title
                if row.get('parent_tool_use_id') or row.get('isSidechain'):
                    continue
                message = row.get('message') or {}
                usage = message.get('usage')
                if row.get('type') == 'assistant' and isinstance(usage, dict):
                    latest = {'tokens': sum(int(usage.get(k) or 0) for k in
                              ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')),
                              'model': message.get('model'), 'timestamp': row.get('timestamp', '')}
                    outs[message.get('id') or len(outs)] = int(usage.get('output_tokens') or 0)
                if row.get('type') == 'result':
                    for model, stats in (row.get('modelUsage') or {}).items():
                        if stats.get('contextWindow'):
                            _windows[model] = int(stats['contextWindow'])
    except OSError:
        return {}
    if not latest:
        return {}
    latest['limit'] = _windows.get(latest['model'])
    latest['output'] = sum(outs.values())
    latest['path'] = path
    latest['mtime'] = os.path.getmtime(path)
    is_code = os.path.normcase(path).startswith(os.path.normcase(CODE_ROOT))
    latest['title'] = ('Code: ' if is_code else 'Cowork: ') + str(title or (None if is_code else _cowork_title(path)) or os.path.basename(os.path.dirname(path))[:20])
    return latest
