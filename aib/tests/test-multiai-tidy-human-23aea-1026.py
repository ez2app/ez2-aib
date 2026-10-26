"""Native human evidence uses exact session identity and read-only public fields."""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT/'src/memory', ROOT/'src/cockpit', ROOT/'src/identity'):
    sys.path.insert(0, str(p))
import memory_tidy as M
import aib_memory
import aib_handoff
import seat_native
SID = '11111111-2222-4333-8444-55555550ab12'
TS = '2026-10-26T05:00:00Z'
SINCE = '2026-10-26T04:00:00Z'

def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128); n >>= 7
    return bytes(out + bytes([n]))

def field(n, value):
    if isinstance(value, str): value = value.encode()
    return varint(n << 3) + varint(value) if isinstance(value, int) else varint((n << 3) | 2) + varint(len(value)) + value

def step(typ, content):
    return field(1, typ) + field(5, field(1, field(1, 1792990800))) + field({14:19, 15:20}[typ], content)

class MultiAI(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        env = {'EZ2_AIB_HOME':str(self.root/'state'), 'EZ2_AIB_STATE_ROOT':str(self.root/'state'),
               'EZ2_AGY_CONV_DIR':str(self.root/'agy'), 'EZ2_COPILOT_SESSION_STATE_DIR':str(self.root/'copilot'),
               'EZ2_GROK_SESSIONS_DIR':str(self.root/'grok')}
        patch = mock.patch.dict(os.environ, env); patch.start(); self.addCleanup(patch.stop)
        self.ctx = {'code':'0ab12', 'seat_uid':SID}
    def write(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        return path
    def read(self, provider, bot=False):
        with mock.patch.object(aib_handoff,'rounds',return_value=[]), mock.patch.object(aib_handoff,'binding_state',return_value={'problem':'bot' if bot else 'ok'}), mock.patch.object(M,'_human_filter',return_value=None):
            return M.human_words(dict(self.ctx, provider=provider), SINCE, span_rounds=True)
    def test_utc_z_window_matches_explicit_offset(self):
        self.assertEqual(M._when(TS), M._when("2026-10-26T13:00:00+08:00"))
        self.assertEqual(M._when(TS).year, 2026)
        self.assertEqual(M._when("not-a-timestamp").year, 1)

    def test_context_retains_runtime_provider_not_model_name(self):
        p=self.root/'state/instances/20261026-000000-ab12/seats/w1.json'
        self.write(p,[{'session_uuid':SID,'provider':'copilot','model_requested':'model-alias'}])
        self.assertEqual(aib_memory.seat_context('ab12/0ab12')['provider'],'copilot')
    def test_codex_session_metadata_wins_over_filename(self):
        import seat_fork
        path=self.root/('rollout-'+SID+'.jsonl')
        user={'type':'response_item','timestamp':TS,'payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'hello'}]}}
        meta={'type':'session_meta','payload':{'id':SID,'session_id':'other'}}
        self.write(path,[meta,user])
        with mock.patch.object(seat_fork,'find_record',return_value=path):
            self.assertEqual(self.read('codex'),[])
            self.assertEqual(M.words_source('0ab12'),M.HUMAN_WORDS_NO_TRANSCRIPT)
            meta['payload']={'id':'thread-id','session_id':SID}
            self.write(path,[meta,user])
            self.assertEqual(self.read('codex'),[('2026-10-26 05:00','hello')])

    def test_copilot_kimi_public_user_only(self):
        rows=[{'type':'session.start','data':{'sessionId':SID}},
              {'type':'user.message','timestamp':TS,'data':{'content':'hello'}},
              {'type':'assistant.message','timestamp':TS,'data':{'content':'private','reasoningText':'secret'}},
              {'type':'user.message','timestamp':TS,'data':{'content':'tool result','parentToolCallId':'t'}},
              {'type':'tool.execution_complete','timestamp':TS,'data':{'content':'secret'}}]
        self.write(self.root/'copilot'/SID/'events.jsonl', rows)
        self.assertEqual(self.read('copilot'),[('2026-10-26 05:00','hello')])
    def test_copilot_wrong_session_is_unavailable(self):
        self.write(self.root/'copilot'/SID/'events.jsonl',[{'type':'session.start','data':{'sessionId':'other'}},{'type':'user.message','timestamp':TS,'data':{'content':'wrong'}}])
        self.assertEqual(self.read('copilot'),[])
        self.assertEqual(M.words_source('0ab12'),M.HUMAN_WORDS_NO_TRANSCRIPT)
    def test_grok_content_and_machine_reminders(self):
        self.write(self.root/'grok/cwd'/SID/'chat_history.jsonl',[
            {'type':'user','timestamp':TS,'content':[{'type':'text','text':'hello'}]},
            {'type':'user','timestamp':TS,'content':'<system-reminder>private</system-reminder>'},
            {'type':'assistant','timestamp':TS,'content':'answer'},
            {'type':'user','timestamp':TS,'content':'private','isMeta':True}])
        self.assertEqual(self.read('grok'),[('2026-10-26 05:00','hello')])
    def test_all_api_history_paths_and_epoch_time(self):
        import seat_fork
        for provider, folder in seat_fork.API_SESSIONS.items():
            with self.subTest(provider=provider):
                self.write(self.root/'state'/folder/SID/'history.jsonl',[
                    {'role':'user','content':'hello','ts':1792990800},
                    {'role':'assistant','content':'answer','ts':1792990800},
                    {'role':'tool','content':'secret','ts':1792990800},
                    {'role':'user','content':'aib_local result: secret','ts':1792990800},
                    {'role':'user','content':'aib_dm result: secret','ts':1792990800},
                    {'role':'user','content':'old','ts':1}])
                self.assertEqual(self.read(provider),[('2026-10-26 05:00','hello')])
    def test_api_unknown_times_and_bot_seats_fail_closed(self):
        self.write(self.root/'state/deepseek-sessions'/SID/'history.jsonl',[
            {'role':'user','content':'unknown'}, {'role':'user','content':'inf','ts':float('inf')},
            {'role':'user','content':'hello','ts':1792990800}])
        self.assertEqual(self.read('deepseek'),[('2026-10-26 05:00','hello')])
        self.assertEqual(M.words_source('0ab12'),M.HUMAN_WORDS_NO_TRANSCRIPT)
        self.assertEqual(self.read('deepseek',bot=True),[])
    def agy_db(self, cascade=SID):
        p=self.root/'agy'/(SID+'.db'); p.parent.mkdir()
        with closing(sqlite3.connect(p)) as con, con:
            con.executescript('CREATE TABLE trajectory_meta(cascade_id TEXT); CREATE TABLE steps(idx INTEGER PRIMARY KEY,step_type INTEGER,step_format INTEGER,step_payload BLOB);')
            con.execute('INSERT INTO trajectory_meta VALUES (?)',(cascade,))
            con.execute('INSERT INTO steps VALUES (0,14,0,?)',(step(14,field(2,'hello')+field(4,field(1,'private context'))),))
            con.execute('INSERT INTO steps VALUES (1,15,0,?)',(step(15,field(1,'answer')+field(3,'private thinking')),))
            con.execute('INSERT INTO steps VALUES (2,132,0,?)',(field(1,132)+field(140,field(1,'tool args')),))
        return p
    def test_agy_gemini_readonly_public_prompt(self):
        p=self.agy_db(); before=hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(self.read('agy'),[('2026-10-26 05:00','hello')])
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)
    def test_agy_wrong_session_or_truncated_history_unavailable(self):
        self.agy_db(cascade='other')
        self.assertEqual(self.read('agy'),[])
        self.assertEqual(M.words_source('0ab12'),M.HUMAN_WORDS_NO_TRANSCRIPT)
    def test_agy_bounded_window_is_not_silence(self):
        self.agy_db()
        with mock.patch.object(seat_native,'agy_tail',return_value=([],[],0,0,[],12)):
            self.assertEqual(self.read('agy'),[])
            self.assertEqual(M.words_source('0ab12'),M.HUMAN_WORDS_NO_TRANSCRIPT)
    def test_malformed_json_records_do_not_become_evidence(self):
        rows=[{'type':'user','timestamp':TS,'message':'invalid'},
              {'type':'user','timestamp':TS,'message':{'content':{'tool':'private'}}},
              {'type':'user','timestamp':TS,'message':{'content':[{'type':'text','text':123}]}},
              {'type':'user.message','timestamp':TS,'data':{'content':{'tool':'private'}}}]
        out=M._human_rows(io.StringIO('\n'.join(json.dumps(r) for r in rows)),SINCE,{'code':'0ab12','human_bound':True},M._FallbackFilter)
        self.assertEqual(out,[])
    def test_unknown_engine_does_not_probe_another_provider(self):
        self.write(self.root/'grok/cwd'/SID/'chat_history.jsonl',[{'type':'user','timestamp':TS,'content':'wrong provider'}])
        self.assertEqual(self.read('unknown'),[])

if __name__=='__main__': unittest.main()
