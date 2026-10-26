#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Incremental JSONL cursors never skip undispatched records or partial writes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/cockpit'))
import seat_panel as SP


def line(uid, content=None, **extra):
    data = {'type': 'assistant', 'uuid': uid, 'timestamp': '2026-10-27T00:00:00Z',
            'message': {'content': [{'type': 'text', 'text': uid}] if content is None else content}}
    data.update(extra)
    return (json.dumps(data, ensure_ascii=False) + '\n').encode()


class CursorPages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'transcript.jsonl'
        self.rec = {'session_uuid': 'session-one', 'provider': 'claude'}
        self.seat = mock.patch.object(SP, 'find_seat', return_value=self.rec)
        self.file = mock.patch.object(SP, 'transcript_path', return_value=str(self.path))
        self.seat.start(); self.file.start()
        self.addCleanup(self.seat.stop); self.addCleanup(self.file.stop)
        self.seed = line('seed'); self.path.write_bytes(self.seed)
        self.offset = len(self.seed)

    def read(self, **kw):
        data, rc = SP.transcript('aa001', **kw)
        self.assertEqual(rc, 0, data)
        return data

    def append(self, data):
        with self.path.open('ab') as f: f.write(data)

    def pages(self, offset, session='session-one', limit=2):
        result, offsets = [], []
        for _ in range(30):
            data = self.read(offset=offset, session=session, limit=limit)
            result.extend(e['id'] for e in data['events']); offsets.append(data['last_offset'])
            if not data['truncated']: return result, offsets
            self.assertGreater(data['last_offset'], offset, 'complete test records must make progress')
            offset, session = data['last_offset'], data['session']
        self.fail('cursor did not reach EOF')

    def test_five_events_limit_two_in_order_without_loss_or_duplicates(self):
        rows = [line('e%d' % i) for i in range(5)]; self.append(b''.join(rows))
        first = self.read(offset=self.offset, session='session-one', limit=2)
        self.assertEqual([e['id'] for e in first['events']], ['e0', 'e1'])
        self.assertEqual(first['last_offset'], self.offset + len(rows[0]) + len(rows[1]))
        self.assertTrue(first['truncated'])
        ids, offsets = self.pages(self.offset)
        self.assertEqual(ids, ['e0', 'e1', 'e2', 'e3', 'e4'])
        self.assertEqual(offsets[-1], self.path.stat().st_size)
        self.assertEqual(self.read(offset=offsets[-1], session='session-one')['events'], [])

    def test_first_load_still_returns_latest_tail(self):
        self.append(b''.join(line('e%d' % i) for i in range(5)))
        first = self.read(offset=0, limit=2)
        self.assertEqual([e['id'] for e in first['events']], ['e3', 'e4'])
        self.assertEqual(first['last_offset'], self.path.stat().st_size)

    def test_partial_utf8_record_and_valid_json_without_newline_wait(self):
        full = line('utf8', [{'type':'text','text':'中文字'}])
        cut = full.index('中'.encode()) + 1
        self.append(full[:cut])
        a = self.read(offset=self.offset, session='session-one')
        self.assertEqual(a['events'], []); self.assertEqual(a['last_offset'], self.offset)
        self.append(full[cut:-1])
        b = self.read(offset=a['last_offset'], session='session-one')
        self.assertEqual(b['events'], []); self.assertEqual(b['last_offset'], self.offset)
        self.append(b'\n')
        c = self.read(offset=b['last_offset'], session='session-one')
        self.assertEqual([e['text'] for e in c['events']], ['中文字'])
        self.assertEqual(c['last_offset'], self.path.stat().st_size)

    def test_first_tail_does_not_consume_partial_final_line(self):
        self.append(line('later')[:-1])
        first = self.read(limit=2)
        self.assertEqual(first['last_offset'], self.offset)
        self.append(b'\n')
        nxt = self.read(offset=first['last_offset'], session=first['session'])
        self.assertEqual([e['id'] for e in nxt['events']], ['later'])

    def test_non_events_and_malformed_complete_lines_can_advance(self):
        noise = b'{"type":"progress"}\nnot-json\n[]\n'
        self.append(noise + line('real') + noise)
        first = self.read(offset=self.offset, session='session-one', limit=1)
        self.assertEqual([e['id'] for e in first['events']], ['real'])
        self.assertEqual(first['last_offset'], self.offset + len(noise) + len(line('real')))
        last = self.read(offset=first['last_offset'], session='session-one', limit=1)
        self.assertEqual(last['events'], []); self.assertEqual(last['last_offset'], self.path.stat().st_size)

    def test_multi_event_record_is_atomic_even_when_it_exceeds_limit(self):
        blocks = [{'type':'text','text':'a'}, {'type':'text','text':'b'},
                  {'type':'tool_use','id':'ask1','name':'AskUserQuestion','input':{'questions':[{'question':'Choose?','options':[]}]}}]
        multi = line('multi', blocks); self.append(multi + line('after'))
        a = self.read(offset=self.offset, session='session-one', limit=2)
        self.assertEqual([e['id'] for e in a['events']], ['multi','multi#2','ask1','ask1:ask'])
        self.assertEqual(a['last_offset'], self.offset + len(multi))
        b = self.read(offset=a['last_offset'], session='session-one', limit=2)
        self.assertEqual([e['id'] for e in b['events']], ['after'])

    def test_session_change_replays_new_file_from_head_in_pages(self):
        self.rec['session_uuid'] = 'session-two'
        self.path.write_bytes(b''.join(line('new%d' % i) for i in range(5)))
        a = self.read(offset=self.offset, session='session-one', limit=2)
        self.assertTrue(a['reset']); self.assertEqual([e['id'] for e in a['events']], ['new0','new1'])
        rest, _ = self.pages(a['last_offset'], 'session-two')
        self.assertEqual(rest, ['new2','new3','new4'])

    def test_truncation_replays_from_head_and_empty_session_is_incremental(self):
        self.path.write_bytes(line('tiny'))
        a = self.read(offset=50000, session='session-one', limit=1)
        self.assertTrue(a['reset']); self.assertEqual([e['id'] for e in a['events']], ['tiny'])
        self.path.write_bytes(b'')
        empty = self.read(offset=a['last_offset'], session='session-one', limit=2)
        self.assertTrue(empty['reset']); self.assertEqual(empty['last_offset'], 0)
        self.append(b''.join(line('new%d' % i) for i in range(5)))
        resumed = self.read(offset=0, session='session-one', limit=2)
        self.assertEqual([e['id'] for e in resumed['events']], ['new0','new1'])

    def test_record_scan_and_byte_budget_are_bounded(self):
        self.append(b''.join(line('e%d' % i) for i in range(1000)))
        with mock.patch.object(SP, '_ts', wraps=SP._ts) as parsed:
            a = self.read(offset=self.offset, session='session-one', limit=2)
        self.assertEqual(parsed.call_count, 2)
        noise = b'{"type":"progress"}\n'
        self.path.write_bytes(self.seed + noise * 20 + line('end'))
        with mock.patch.object(SP, 'INCREMENT_MAX_BYTES', len(noise)*3):
            b = self.read(offset=self.offset, session='session-one', limit=0)
            self.assertEqual(b['last_offset'], self.offset + len(noise)*3)
            self.assertEqual(b['events'], []); self.assertTrue(b['truncated'])
            ids, _ = self.pages(self.offset)
        self.assertEqual(ids, ['end'])

    def test_oversized_record_fails_without_success_cursor(self):
        self.append(line('large', [{'type':'text','text':'x'*400}]))
        with mock.patch.object(SP, 'INCREMENT_MAX_LINE', 100):
            data, rc = SP.transcript('aa001', offset=self.offset, session='session-one')
        self.assertEqual(rc, 4); self.assertEqual(data['why'], 'transcript-record-too-large')
        self.assertNotIn('last_offset', data)


if __name__ == '__main__': unittest.main()
