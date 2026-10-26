import io, json, sys, unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/"src/memory",ROOT/"src/cockpit",ROOT/"src/identity"):
 sys.path.insert(0,str(p))
import memory_tidy as M
import seat_fork, aib_handoff
class CodexHuman(unittest.TestCase):
 def row(self,text,role="user",**extra):
  return dict(type="response_item",timestamp="2026-10-26T09:00:00Z",payload=dict(type="message",role=role,content=[dict(type="input_text",text=text)]),**extra)
 def read(self,rows,since=""):
  return M._human_rows(io.StringIO("\n".join(json.dumps(r) for r in rows)),since,{"code":"abcde","human_bound":True},M._FallbackFilter)
 def test_real_user_and_time(self):
  self.assertEqual(self.read([self.row("把能合的全合、能修先修")]),[("2026-10-26 09:00","把能合的全合、能修先修")])
  self.assertEqual(self.read([self.row("本人原話")],"2026-10-26T10:00:00+00:00"),[])
 def test_no_assistant_tools_developer_or_machine(self):
  rows=[self.row("private","assistant"),self.row("policy","developer"),self.row("meta",isMeta=True),self.row("summary",isCompactSummary=True),self.row("# AGENTS.md instructions\n政策"),self.row("<environment_context>機器</environment_context>"),dict(type="event_msg",payload=dict(type="user_message",message="duplicate")),dict(type="response_item",payload=dict(type="function_call_output",output="private"))]
  self.assertEqual(self.read(rows),[])
 def test_native_tool_result_block_is_not_human(self):
  row=self.row("tool material")
  row["payload"]["content"].append(dict(type="tool_result",content="private"))
  self.assertEqual(self.read([row]),[])
 def test_native_dm_header_is_not_human(self):
  row=self.row("IP:127.0.0.1/abcd/系統@aib >> IP:127.0.0.1/abcd/工程師@abcde 10-26 17:29(台北時間) 機器提醒")
  self.assertEqual(self.read([row]),[])
 def test_legacy_claude_preserved(self):
  row=dict(type="user",timestamp="2026-10-26T09:00:00Z",message=dict(content=[dict(type="text",text="繼續修正")]))
  self.assertEqual(self.read([row])[0][1],"繼續修正")
 def test_malformed_rows_ignored(self):
  self.assertEqual(self.read([None,[],dict(type="response_item",payload=[]),dict(type="response_item",payload=dict(type="message",role="user",content="not a list"))]),[])
 def test_review_does_not_truncate_requests(self):
  ctx={"code":"abcde","seat_uid":"00000000-0000-0000-0000-0000000abcde"}
  rows=[("2026-10-26 09:00",str(i)) for i in range(45)]
  with mock.patch.object(aib_handoff,"rounds",return_value=[]),mock.patch.object(aib_handoff,"binding_state",return_value={"problem":"ok"}),mock.patch.object(seat_fork,"find_transcript",return_value=Path(__file__)),mock.patch.object(M,"_human_filter",return_value=None),mock.patch.object(M,"_human_rows",return_value=rows):
   self.assertEqual(len(M.human_words(ctx,span_rounds=True)),45)
   self.assertEqual(len(M.human_words(ctx)),M.HUMAN_MAX)
 def test_discovery_same_uuid(self):
  ctx={"code":"abcde","seat_uid":"00000000-0000-0000-0000-0000000abcde"}
  with mock.patch.object(aib_handoff,"rounds",return_value=[]),mock.patch.object(seat_fork,"find_transcript",return_value=None),mock.patch.object(seat_fork,"find_record",return_value=None) as find:
   self.assertEqual(M.human_words(ctx),[])
   find.assert_called_once_with("codex",ctx["seat_uid"])
if __name__=="__main__":unittest.main()
