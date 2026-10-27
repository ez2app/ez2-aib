#!/usr/bin/env python3
"""Approving one session never authorizes its replacement; no live tmux/Touch ID."""
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('act_base', HERE/'test-seat-act-d07f9-1020.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
CODE, SESSION, INSTANCE=base.CODE,base.SESS,'20261020-000000-ab12'

class SessionGuard(base.Box):
    def record(self):
        return self.state/'instances'/INSTANCE/'seats'/'w3.json'
    def change(self,key,value):
        p=self.record();r=json.loads(p.read_text());r[key]=value;p.write_text(json.dumps(r))
    def action(self,kind,**kw):
        if kind=='input':
            return self.act(self.SA.do_input,CODE,'hello',screen=base.PROMPT,**kw)
        if kind=='answer':
            return self.act(self.SA.do_answer,CODE,'yes',screen=base.PERMISSION,**kw)
        return self.act(self.SA.do_stop,CODE,**kw)
    def test_expectation_mismatch_before_approval(self):
        for kind in ('input','answer','stop'):
            for kwargs in ({'expected_session':'other'},{'expected_instance':'another-cabin'}):
                out,rc=self.action(kind,**kwargs)
                self.assertEqual((rc,out['why']),(2,'stale-session'))
        self.assertEqual(self.asked,[]);self.assertEqual(self.keys(),[])
    def test_expected_identity_compatible(self):
        for kind in ('input','answer','stop'):
            out,rc=self.action(kind,expected_session=SESSION,expected_instance=INSTANCE)
            self.assertEqual((rc,out['ok']),(0,True),out)
    def test_rebinding_during_approval_never_sends(self):
        old=self.record().read_text()
        for kind in ('input','answer','stop'):
            for key,value in (('session_uuid','99999999-2222-4333-8444-5555550ab12'),('socket','aihub-other'),('window_id','@9'),('pid',22222),('retired',True)):
                with self.subTest(kind=kind,key=key):
                    self.record().write_text(old)
                    def approve(reason):
                        self.change(key,value)
                        return {'ok':True,'why':'ok'}
                    out,rc=self.action(kind,touchid=approve,expected_session=SESSION,expected_instance=INSTANCE)
                    self.assertEqual((rc,out['why']),(2,'stale-session'),out)
                    self.assertEqual(self.keys(),[])
                    self.assertEqual(self.SA.unlock_left(CODE),0,'stale input must not grant an unlock')
    def test_removed_record_during_approval(self):
        def approve(reason):
            self.record().unlink();return {'ok':True,'why':'ok'}
        out,rc=self.action('stop',touchid=approve)
        self.assertEqual((rc,out['why']),(2,'stale-session'));self.assertEqual(self.keys(),[])
    def test_newer_same_code_record_during_approval(self):
        def approve(reason):
            r=json.loads(self.record().read_text());r['started_epoch']=100;r['window_id']='@4'
            self.record().with_name('w4.json').write_text(json.dumps(r))
            return {'ok':True,'why':'ok'}
        out,rc=self.action('stop',touchid=approve)
        self.assertEqual((rc,out['why']),(2,'stale-session'));self.assertEqual(self.keys(),[])
    def test_prompt_changes_during_approval(self):
        for last,why in (({'ask':{'id':'new'},'at_prompt':False},'asking'),({'at_prompt':False},'not-at-prompt')):
            with mock.patch.object(self.SA.SPN,'pending',side_effect=[({'at_prompt':True},0),(last,0)]):
                out,rc=self.action('input')
            self.assertEqual((rc,out['why']),(2,why));self.assertEqual(self.keys(),[])
            self.assertEqual(self.SA.unlock_left(CODE),0)
    def test_answer_changes_during_approval(self):
        ask={'id':'q1','prompt':'one','options':[{'key':'1','label':'Yes'}],'source':'screen','multi':False}
        for last in (None,dict(ask,id='q2'),dict(ask,prompt='different')):
            with mock.patch.object(self.SA.SPN,'pending',side_effect=[({'ask':ask},0),({'ask':last},0)]):
                out,rc=self.action('answer')
            self.assertEqual((rc,out['why']),(2,'stale'));self.assertEqual(self.keys(),[])
    def test_cli_flags_forwarded(self):
        for command,fn,extra in (('input','do_input',['--text','hi']),('answer','do_answer',['yes']),('stop','do_stop',[])):
            with mock.patch.object(self.SA,fn,return_value=({'ok':True},0)) as call:
                rc=self.SA.main([command,CODE,*extra,'--expected-session',SESSION,'--expected-instance',INSTANCE,'--json'],out=io.StringIO())
                self.assertEqual(rc,0)
                self.assertEqual(call.call_args.kwargs['expected_session'],SESSION)
                self.assertEqual(call.call_args.kwargs['expected_instance'],INSTANCE)

if __name__=='__main__':unittest.main()
