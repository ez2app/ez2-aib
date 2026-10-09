After header+tz+datetime in _emit_usage_block, call:
  _emit_usage_subtab_row(emit, links, unlocked, subtab)
If unlocked and subtab==usage: overview active-only (no accounts panel).
If unlocked and subtab==codex: call _emit_usage_accounts_panel only.
Remove old behavior that appends accounts panel under overview on unlock.
