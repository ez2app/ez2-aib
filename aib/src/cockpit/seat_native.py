# SPDX-License-Identifier: Apache-2.0
"""Read-only AGY, Copilot and Grok chat adapters. No CLI calls or journal writes.

AGY wire fields were checked against the installed CLI's embedded trajectory and
cortex descriptors. Decode only explicit public-message fields, never arbitrary
UTF-8 strings (planner thinking, tool arguments and system prompts stay private).
"""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from urllib.parse import quote

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
MAX_BLOB = 4 * 1024 * 1024
MAX_BYTES = 32 * 1024 * 1024
MAX_STEPS = 8192


@contextmanager
def _db(path):
    con = sqlite3.connect('file:' + quote(os.path.abspath(path), safe='/') + '?mode=ro', uri=True, timeout=0.2)
    try:
        con.execute('PRAGMA query_only=ON')
        con.set_progress_handler(lambda: 1, 1000000)
        yield con
    finally:
        con.close()


def native_path(rec):
    sid = str(rec.get('session_uuid') or '').lower()
    if not UUID.fullmatch(sid):
        return None
    provider = rec.get('provider')
    if provider == 'agy':
        root = os.environ.get('EZ2_AGY_CONV_DIR') or os.path.expanduser('~/.gemini/antigravity-cli/conversations')
        path = os.path.join(root, sid + '.db')
        if not os.path.isfile(path):
            return None
        try:
            with _db(path) as con:
                rows = con.execute('SELECT cascade_id FROM trajectory_meta LIMIT 2').fetchall()
                return path if any(r[0] == sid for r in rows) else None
        except sqlite3.Error:
            return None
    if provider == 'copilot':
        root = os.environ.get('EZ2_COPILOT_SESSION_STATE_DIR') or os.path.expanduser('~/.copilot/session-state')
        path = os.path.join(root, sid, 'events.jsonl')
        try:
            with open(path, encoding='utf-8') as fh:
                first = json.loads(fh.readline())
            if first.get('type') == 'session.start' and first.get('data', {}).get('sessionId') == sid:
                return path
        except (OSError, ValueError, AttributeError):
            pass
        return None
    if provider == 'grok':
        import grok_ctx_probe
        folder = grok_ctx_probe.session_dir(sid, rec.get('cwd'))
        path = os.path.join(folder, 'chat_history.jsonl') if folder else ''
        return path if os.path.isfile(path) else None
    return None


def json_message(rec, path, raw):
    """Return a Claude-shaped public record, or None for unsupported/internal events."""
    copilot = os.path.basename(path) == 'events.jsonl'
    provider = 'copilot' if copilot else 'grok'
    if copilot:
        data = rec.get('data')
        if not isinstance(data, dict):
            return None
        role = {'user.message': 'user', 'assistant.message': 'assistant', 'session.error': 'system'}.get(rec.get('type'))
        text = data.get('message') if role == 'system' else data.get('content')
        sidechain = role == 'assistant' and bool(data.get('parentToolCallId'))
    else:
        role = rec.get('type')
        if role not in ('user', 'assistant'):
            return None
        text = rec.get('content')
        sidechain = False
    if role not in ('user', 'assistant', 'system') or not isinstance(text, (str, list)):
        return None
    if isinstance(text, list):
        blocks = [x for x in text if isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str)]
    else:
        blocks = [{'type': 'text', 'text': text}]
    out = {'type': role, 'uuid': rec.get('id') or hashlib.sha1(raw).hexdigest()[:20],
           'timestamp': rec.get('timestamp'), 'message': {'role': role, 'content': blocks},
           'isSidechain': sidechain, '_native_provider': provider}
    if role == 'system':
        out.update(subtype='informational', content=text)
    return out


def _varint(raw, pos):
    value = 0
    for n in range(10):
        if pos >= len(raw):
            raise ValueError('truncated varint')
        byte = raw[pos]; pos += 1
        value |= (byte & 127) << (7 * n)
        if byte < 128:
            return value, pos
    raise ValueError('oversized varint')


def _wire(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BLOB:
        raise ValueError('invalid protobuf size')
    result = {}; pos = 0
    while pos < len(raw):
        key, pos = _varint(raw, pos); number, wire = key >> 3, key & 7
        if not number:
            raise ValueError('invalid protobuf field')
        if wire == 0:
            value, pos = _varint(raw, pos)
        elif wire == 2:
            size, pos = _varint(raw, pos)
            if size > len(raw) - pos:
                raise ValueError('truncated protobuf field')
            value = raw[pos:pos+size]; pos += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if size > len(raw) - pos:
                raise ValueError('truncated fixed field')
            value = raw[pos:pos+size]; pos += size
        else:
            raise ValueError('unsupported protobuf wire type')
        result[number] = value
    return result


def _text(fields, key):
    value = fields.get(key)
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else ''


def _step(idx, step_type, raw):
    try:
        top = _wire(raw)
        if top.get(1) != step_type:
            return None
        metadata = _wire(top.get(5, b''))
        stamp = _wire(metadata.get(1, b''))
        seconds, nanos = stamp.get(1), stamp.get(2, 0)
        ts = seconds + nanos / 1e9 if isinstance(seconds, int) and isinstance(nanos, int) and 0 <= nanos < 1000000000 else None
        text = ''; role = 'system'
        if step_type == 14:
            fields = _wire(top.get(19, b'')); text = _text(fields, 2) or _text(fields, 1); role = 'user'
        elif step_type == 15:
            fields = _wire(top.get(20, b'')); text = _text(fields, 8) or _text(fields, 1); role = 'assistant'
        elif step_type == 82:
            text = _text(_wire(top.get(94, b'')), 2); role = 'assistant'
        elif step_type == 17:
            fields = _wire(top.get(24, b''))
            if fields.get(5):
                text = _text(_wire(fields.get(3, b'')), 1)
        if not text and isinstance(top.get(31), bytes):
            text = _text(_wire(top[31]), 1); role = 'system'
        if not text.strip():
            return None
        return {'id': 'agy:%d:%s' % (idx, hashlib.sha1(raw).hexdigest()[:16]), 'ts': ts,
                'kind': role, 'text': text, 'meta': {'provider': 'agy', 'step': idx}}
    except (ValueError, TypeError, OverflowError):
        return None


def agy_tail(path, enough, marker=None):
    """SQLite is snapshot-polled, not byte-tailed. A changed step gets a new revision ID."""
    bound = None
    match = re.fullmatch(r'agy:(\d+):[0-9a-f]+', marker or '')
    if match:
        bound = int(match.group(1))
    window = 256
    with _db(path) as con:
        while True:
            where = ' WHERE idx <= ?' if bound is not None else ''
            args = (bound, window) if bound is not None else (window,)
            rows = con.execute('SELECT idx, step_type, CASE WHEN step_type IN (14,15,17,82) AND step_format=0 THEN step_payload END FROM steps' + where + ' ORDER BY idx DESC LIMIT ?', args)
            events = []; count = size = 0; oldest = 0
            for idx, typ, raw in rows:
                count += 1; oldest = idx
                if raw is None:
                    continue
                size += len(raw)
                if size > MAX_BYTES:
                    break
                event = _step(idx, typ, raw)
                if event:
                    events.append(event)
            events.reverse()
            if count < window or oldest == 0 or enough(events) or window >= MAX_STEPS or size > MAX_BYTES:
                return events, [], 0, 0, [], max(0, oldest)
            window = min(MAX_STEPS, window * 2)


def grok_notice(path):
    """Latest failed turn only; do not copy prompts, hook output or intermediate chunks."""
    updates = os.path.join(os.path.dirname(path), 'updates.jsonl')
    try:
        with open(updates, 'rb') as fh:
            fh.seek(0, 2); end = fh.tell(); off = max(0, end - 512*1024); fh.seek(off)
            if off:
                fh.readline()
            lines = fh.readlines()
    except OSError:
        return None
    latest = None
    for line in lines:
        try:
            rec = json.loads(line); params = rec.get('params', {}); update = params.get('update', {})
            if params.get('sessionId') not in (None, os.path.basename(os.path.dirname(path))):
                continue
            if not isinstance(update, dict):
                continue
            typ = update.get('sessionUpdate')
            if typ == 'turn_completed' and update.get('stop_reason') != 'error':
                latest = None
            if typ == 'retry_state' and update.get('type') == 'failed':
                latest = (rec, line, update.get('message'))
            elif typ == 'turn_completed' and update.get('stop_reason') == 'error' and latest is None:
                latest = (rec, line, update.get('agent_result'))
        except (ValueError, AttributeError):
            continue
    if not latest or not isinstance(latest[2], str):
        return None
    rec, raw, text = latest
    return {'type': 'system', 'subtype': 'informational', 'content': text[:8000],
            'uuid': 'grok-error:' + hashlib.sha1(raw).hexdigest()[:16], 'timestamp': rec.get('timestamp'),
            '_native_provider': 'grok'}
