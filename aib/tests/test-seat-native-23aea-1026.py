#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Provider chat integration: native cursors, public-only fields and read-only SQLite."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/cockpit'))
import seat_panel as SP
import seat_native as N
SID = '11111111-2222-4333-8444-55555550ab12'
CODE = '0ab12'
TS = '2026-10-26T05:00:00Z'


def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128); n >>= 7
    out.append(n)
    return bytes(out)


def field(n, value):
    if isinstance(value, str): value = value.encode()
    return varint(n << 3) + varint(value) if isinstance(value, int) else varint((n << 3) | 2) + varint(len(value)) + value


def step(typ, content):
    slot = {14: 19, 15: 20, 82: 94}[typ]
    meta = field(1, field(1, 1792990800))
    return field(1, typ) + field(5, meta) + field(slot, content)


class Native(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name); self.state = self.root/'state'
        seats = self.state/'instances/20261026-000000-ab12/seats'; seats.mkdir(parents=True)
        self.record = seats/'w1.json'
        self.env = mock.patch.dict(os.environ, {'EZ2_AIB_STATE_ROOT': str(self.state),
            'EZ2_AGY_CONV_DIR': str(self.root/'agy'), 'EZ2_COPILOT_SESSION_STATE_DIR': str(self.root/'copilot'),
            'EZ2_GROK_SESSIONS_DIR': str(self.root/'grok'), 'EZ2_AIB_LOCALE': 'zh-TW'})
        self.env.start(); self.addCleanup(self.env.stop)

    def seat(self, provider):
        self.record.write_text(json.dumps({'session_uuid': SID, 'provider': provider, 'cwd': str(self.root), 'started_epoch': 1}))

    def transcript(self, **kw):
        d, rc = SP.transcript(CODE, state_root=str(self.state), **kw)
        self.assertEqual(rc, 0, d)
        return d

    def copilot(self, rows):
        self.seat('copilot'); p = self.root/'copilot'/SID/'events.jsonl'; p.parent.mkdir(parents=True)
        first = {'type': 'session.start', 'data': {'sessionId': SID}}
        p.write_text('\n'.join(json.dumps(x) for x in [first]+rows)+'\n'); return p

    def agy(self, rows):
        self.seat('agy'); p = self.root/'agy'/(SID+'.db'); p.parent.mkdir()
        with sqlite3.connect(p) as con:
            con.executescript('CREATE TABLE trajectory_meta(cascade_id TEXT); CREATE TABLE steps(idx INTEGER PRIMARY KEY,step_type INTEGER,step_format INTEGER,step_payload BLOB);')
            con.execute('INSERT INTO trajectory_meta VALUES (?)',(SID,))
            for idx,typ,raw in rows:con.execute('INSERT INTO steps VALUES (?,?,0,?)',(idx,typ,raw))
        return p

    def test_copilot_visible_messages_error_and_incremental(self):
        p = self.copilot([
            {'id':'u','type':'user.message','timestamp':TS,'data':{'content':'hello','parentAgentTaskId':'root-task'}},
            {'id':'internal','type':'system.message','data':{'content':'private system prompt'}},
            {'id':'a','type':'assistant.message','timestamp':TS,'data':{'content':'answer','reasoningText':'private thinking'}},
            {'id':'child','type':'assistant.message','data':{'content':'child answer','parentToolCallId':'tool'}},
            {'id':'error','type':'session.error','timestamp':TS,'data':{'message':'Authorization error','errorType':'authorization'}},
        ])
        d = self.transcript()
        self.assertEqual([(x['kind'],x['text']) for x in d['events']], [('user','hello'),('assistant','answer'),('system','Authorization error')])
        self.assertEqual(self.transcript(since='a')['events'][0]['id'],'error')
        with p.open('a') as f:f.write(json.dumps({'id':'next','type':'assistant.message','data':{'content':'next reply'}})+'\n')
        self.assertEqual(self.transcript(offset=d['last_offset'])['events'][0]['text'],'next reply')

    def test_grok_injected_reminders_are_hidden(self):
        rec = {'type':'user','content':[{'type':'text','text':'<system-reminder>private skills</system-reminder>'}]}
        self.assertIsNone(N.json_message(rec, '/tmp/chat_history.jsonl', b'reminder'))

    def test_copilot_wrong_session_and_invalid_glob(self):
        p = self.copilot([]); p.write_text(json.dumps({'type':'session.start','data':{'sessionId':'other'}})+'\n')
        self.assertIsNone(N.native_path({'provider':'copilot','session_uuid':SID}))
        self.assertIsNone(N.native_path({'provider':'agy','session_uuid':'*'}))

    def test_agy_messages_never_decode_thinking_and_database_is_unchanged(self):
        user = step(14,field(2,'question')+field(4,field(1,'private context')))
        answer = step(15,field(1,'raw answer')+field(8,'public answer')+field(3,'private thinking')+field(16,'raw secret'))
        p = self.agy([(0,14,user),(1,15,answer),(2,132,field(1,132)+field(140,field(1,'private tool args')))])
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        d = self.transcript()
        self.assertEqual([(e['kind'],e['text']) for e in d['events']],[('user','question'),('assistant','public answer')])
        self.assertEqual(d['last_offset'],0)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)
        self.assertEqual(N._step(3,15,b'\x0a\xff'),None)

    def test_agy_mutable_step_resets_old_revision_and_supports_history(self):
        p = self.agy([(i,15,step(15,field(1,'reply '+str(i)))) for i in range(300)])
        first = self.transcript(limit=2); last = first['last_id']
        self.assertEqual(self.transcript(before=last,limit=2)['events'][-1]['text'],'reply 298')
        with sqlite3.connect(p) as con:con.execute('UPDATE steps SET step_payload=? WHERE idx=299',(step(15,field(1,'updated reply')),))
        after = self.transcript(since=last,limit=2)
        self.assertTrue(after['reset']);self.assertEqual(after['events'][-1]['text'],'updated reply')
        self.assertTrue(self.transcript(offset=100)['reset'])

    def test_agy_rejects_wrong_database_identity(self):
        p = self.agy([])
        with sqlite3.connect(p) as con:con.execute('UPDATE trajectory_meta SET cascade_id="other"')
        d,rc=SP.transcript(CODE,state_root=str(self.state));self.assertEqual((rc,d['why']),(4,'no-transcript'))

    def test_grok_messages_latest_failure_and_recovery(self):
        self.seat('grok'); folder=self.root/'grok/project'/SID;folder.mkdir(parents=True)
        p=folder/'chat_history.jsonl';p.write_text('\n'.join(json.dumps(x) for x in [
            {'type':'system','content':'private system prompt'}, {'type':'user','content':[{'type':'text','text':'question'}]},
            {'type':'assistant','content':'answer','reasoning':'private reasoning','tool_calls':[{'arguments':'private tool args'}]}])+'\n')
        updates=folder/'updates.jsonl'
        def update(value):return json.dumps({'timestamp':TS,'params':{'sessionId':SID,'update':value}})+'\n'
        updates.write_text(update({'sessionUpdate':'retry_state','type':'failed','message':'usage balance exhausted'}))
        d=self.transcript();self.assertEqual([e['text'] for e in d['events']],['question','answer','usage balance exhausted'])
        self.assertEqual(self.transcript(since=d['last_id'])['events'],[])
        with updates.open('a') as f:f.write(update({'sessionUpdate':'turn_completed','stop_reason':'end_turn'}))
        self.assertEqual([e['text'] for e in self.transcript()['events']],['question','answer'])


if __name__ == '__main__': unittest.main()
