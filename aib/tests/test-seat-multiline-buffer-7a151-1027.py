#!/usr/bin/env python3
"""Two subprocess-backed fake tmux clients must not share multiline paste buffers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'/'cockpit'))
import seat_act as A

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root=pathlib.Path(os.environ['FAKE_TMUX_DIR']);root.mkdir(exist_ok=True)
a=sys.argv[1:];cmd=a[2];args=a[3:]
fd=os.open(root/'calls.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
os.write(fd,(json.dumps(a)+'\n').encode());os.close(fd)
if cmd in ('load-buffer','paste-buffer','delete-buffer'):
    name=args[args.index('-b')+1]
    buffers=root/'buffers';buffers.mkdir(exist_ok=True)
    p=buffers/name
if cmd=='load-buffer':
    text=pathlib.Path(args[-1]).read_text();p.write_text(text)
    if os.environ.get('FAKE_TMUX_FAIL')=='load':sys.exit(1)
    if os.environ.get('FAKE_TMUX_BARRIER')=='1':
        (root/('loaded-'+text[0])).write_text('ready')
        until=time.monotonic()+3
        while not all((root/('loaded-'+x)).exists() for x in ('A','B')):
            if time.monotonic()>until:sys.exit(2)
            time.sleep(.005)
elif cmd=='paste-buffer':
    if os.environ.get('FAKE_TMUX_FAIL')=='paste':sys.exit(1)
    if not p.exists():sys.exit(1)
    (root/('received-'+args[-1])).write_text(p.read_text())
    if '-d' in args:p.unlink()
elif cmd=='delete-buffer':
    p.unlink(missing_ok=True)
'''

class Buffers(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.script=self.root/'fake-tmux'
        self.script.write_text(FAKE);self.script.chmod(0o755)
        env=patch.dict(os.environ,{'FAKE_TMUX_DIR':str(self.root)},clear=False);env.start();self.addCleanup(env.stop)
    def send(self,pane,text):
        return A.send_text({'socket':'aihub-fixture','window_id':pane},text,tmux=str(self.script))
    def calls(self):
        return [json.loads(line) for line in (self.root/'calls.jsonl').read_text().splitlines()]
    def test_parallel_two_seats_receive_their_own_text(self):
        results=[]
        with patch.dict(os.environ,{'FAKE_TMUX_BARRIER':'1'}):
            threads=[threading.Thread(target=lambda p,t:results.append(self.send(p,t)),args=(p,t)) for p,t in (('@1','A\nmessage'),('@2','B\nmessage'))]
            for thread in threads:thread.start()
            for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(results,[(True,''),(True,'')])
        self.assertEqual((self.root/'received-@1').read_text(),'A\nmessage')
        self.assertEqual((self.root/'received-@2').read_text(),'B\nmessage')
        calls=self.calls();names=[a[a.index('-b')+1] for a in calls if a[2]=='load-buffer']
        self.assertEqual(len(set(names)),2);self.assertEqual(list((self.root/'buffers').iterdir()),[])
        for a in calls:
            if a[2]=='load-buffer':self.assertFalse(Path(a[-1]).exists())
    def test_partial_load_and_paste_failure_delete_own_buffer(self):
        for stage in ('load','paste'):
            with self.subTest(stage=stage),patch.dict(os.environ,{'FAKE_TMUX_FAIL':stage}):
                result=self.send('@1','A\nmessage')
                self.assertFalse(result[0]);self.assertEqual(list((self.root/'buffers').iterdir()),[])
        calls=self.calls();self.assertFalse(any(a[2]=='send-keys' for a in calls))
        self.assertEqual(sum(a[2]=='delete-buffer' for a in calls),2)
    def test_timeout_cleanup_does_not_mask_error(self):
        calls=[]
        def run(args,**kw):
            calls.append(args)
            if 'delete-buffer' in args:raise FileNotFoundError('tmux exited')
            raise subprocess.TimeoutExpired(args,10)
        with self.assertRaises(subprocess.TimeoutExpired):
            A.send_text({'socket':'aihub-fixture','window_id':'@1'},'A\nmessage',run=run)
        self.assertEqual(calls[1][3],'delete-buffer')
        self.assertEqual(calls[0][5],calls[1][5]);self.assertFalse(Path(calls[0][-1]).exists())

if __name__=='__main__':unittest.main()
