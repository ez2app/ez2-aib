#!/usr/bin/env python3
"""Codex borderless composer detection, using fixed UI structure rather than any ›."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'/'cockpit'))
import seat_ask_patterns as P

FOOT='  Main [default]                     18% context left   ⚠ 3 warnings · f2 to view'
IDLE='› Ask Codex to do anything\n\n'+FOOT

class Codex(unittest.TestCase):
    def reject(self,screen,why=None):
        self.assertFalse(P.at_prompt(screen,'codex'),repr(screen))
        if why:self.assertEqual(P.prompt_block(screen,'codex')[0],why)
    def test_real_empty_and_placeholder_with_footer(self):
        for prompt in ('›','› ','›\u00a0','› Ask Codex to do anything'):
            for footer in (FOOT,'  ? for shortcuts              93% context left'):
                screen=prompt+'\n\n'+footer+'\n'*12
                self.assertTrue(P.at_prompt(screen,'codex'),screen)
                self.assertEqual(P.busy_state(screen,'codex'),'idle')
    def test_working_overrides_empty_composer_and_blank_gap(self):
        for gap in (2,22,45):
            screen='• Working (3m 26s • esc to interrupt)\n'+'\n'*gap+'Tip: Try a shortcut.\n\n'+IDLE
            self.reject(screen,'thinking');self.assertEqual(P.busy_state(screen,'codex'),'thinking')
        self.reject(IDLE+'\n  tab to queue message','thinking')
    def test_draft_not_overridden_by_history_empty_prompt(self):
        screen='›\n\nold response\n\n› private draft text\n\n'+FOOT
        self.reject(screen,'draft')
        self.assertNotIn('private',P.prompt_block(screen,'codex')[1])
        self.reject('› Ask Codex to do anything EXTRA\n'+FOOT,'draft')
    def test_continuations_and_unknown_suffix_fail_closed(self):
        for suffix in ('  multiline draft','```','new output','  › indented quote'):
            self.reject('›\n'+suffix+'\n'+FOOT)
            self.reject(IDLE+'\n'+suffix)
    def test_footer_shaped_draft_and_duplicate_footer_are_rejected(self):
        for draft in ('  Main [default] delete the backup', '93% context left is the title I am drafting', FOOT):
            self.reject('›\n'+draft+'\n\n'+FOOT)
        for footer in ('Main [default]', 'Main [default] delete the backup',
                       '93% context left is the title I am drafting',
                       '? for shortcuts delete the backup',
                       'Main [default] 18% context left extra words'):
            self.reject('›\n'+footer)
    def test_history_and_markdown_are_not_composer(self):
        for screen in ('›\nassistant text','› Ask Codex to do anything\n'+FOOT+'\nnew output',
                       '    ›\n'+FOOT,'> ›\n'+FOOT,'›','› Ask Codex to do anything'):
            self.reject(screen)
    def test_permission_and_selector(self):
        screen='Would you like to run the following command?\n\n  $ ls\n\n› 1. Yes, proceed (y)\n  2. No, and tell Codex what to do differently (esc)\n\n  Press enter to confirm or esc to cancel'
        self.reject(screen,'permission')
        ask=P.permission_ask(screen,'codex');self.assertEqual(len(ask['options']),2)
        self.reject('  /model    choose what model and reasoning effort to use\n'+IDLE,'permission')
        self.reject('Select a model\n'+IDLE,'permission')
    def test_plain_working_word_is_not_activity(self):
        for text in ('We are Working on the code.','The word Working appears here.','• Working on documentation is an example.'):
            self.assertTrue(P.at_prompt(text+'\n'+IDLE,'codex'))
    def test_provider_specific_and_ansi(self):
        self.assertFalse(P.at_prompt(IDLE,'claude'))
        self.assertTrue(P.at_prompt('\x1b[2m› Ask Codex to do anything\x1b[0m\n'+FOOT,'codex'))
        self.assertTrue(P.at_prompt('╭────╮\n│ ❯  │\n╰────╯','claude'))
    def test_historical_permission_followed_by_current_composer(self):
        text='Would you like to run the following command?\n› 1. Yes\n  2. No\n\nApproved.\n'+IDLE
        self.assertIsNone(P.permission_ask(text,'codex'))
        self.assertTrue(P.at_prompt(text,'codex'))

if __name__=='__main__':unittest.main()
