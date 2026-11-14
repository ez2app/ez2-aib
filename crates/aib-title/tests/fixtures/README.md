# beat-golden.json

What the frozen 1.x `aib/src/cockpit/title/title_beat.py` returns for 1264 cases, one
object per case (`fn`, `args`, `want`). `tests/beat_golden.rs` runs `aib_title::beat`
on every case and compares. Nothing in the build or in CI runs Python; the table was
written once, by hand, from the frozen file (519d0 decision 11-13: allowed as a fixture
because no `.py` in the repo changes and CI does not run it).

## Where it came from

| | |
|---|---|
| frozen file | `aib/src/cockpit/title/title_beat.py`, git blob `293bd104a06f0a5a8eda3dc8cb81117ba6c9dac5` (last commit e89a3f0ae4a6d41762e19f3c0abd2d35c56d0f78) |
| python | Python 3.15.0, run with `-I` |
| output sha256 | `3516db365241cae829472274454aa75b53ba03c8d4a5df0145de714f401e5bd4` |

`seat_address.label_of` is replaced by a stub that returns the seat's `_label` field, so
the label path is driven from the table instead of from this machine's seat files.

## Writing it again

Only needed if `title_beat.py` changes (its blob is no longer the one above). Copy the
generator below to a file outside the repo, for example `/tmp/gen-beat-golden.txt`
(it is kept here as text on purpose: the repo carries no new `.py`), then from the
repository root:

```sh
git rev-parse HEAD:aib/src/cockpit/title/title_beat.py   # compare with the blob above
python3 -I /tmp/gen-beat-golden.txt aib/src/cockpit/title \
  > crates/aib-title/tests/fixtures/beat-golden.json
shasum -a 256 crates/aib-title/tests/fixtures/beat-golden.json
```

With the same blob and Python 3.15.0 the sha256 matches the one above. If the blob
changed, the Rust port has to follow the new Python first; update this table of
provenance in the same commit.

## Generator

```python
import sys, json, types, itertools
sys.path.insert(0, sys.argv[1])
sa = types.ModuleType("seat_address")
sa.label_of = lambda seat: seat.get("_label")
sys.modules["seat_address"] = sa
import title_beat as tb

out = []
def add(fn, args, got):
    out.append({"fn": fn, "args": args, "want": got})

NOW = 1794576000
NOW_MS = NOW * 1000 + 123

# trust_light: None record (the sampler path) and typed records
for bind in [None, {"status": "pending"}, {"status": "approved_pending_ea1"}]:
    add("trust_light", {"record": None, "bind": bind and bind["status"], "now_ms": NOW_MS},
        tb.trust_light(None, bind, NOW_MS))
for beat_off, ver in itertools.product([None, -60, -20*60, -15*60, -15*60-1, -3600, -3601, -3*3600], [None, True, False]):
    for bind in [None, "pending", "approved_pending_ea1"]:
        rec = {"sig": "x"}
        if beat_off is not None: rec["beat_epoch"] = NOW + beat_off
        if ver is not None: rec["registry_verified"] = ver
        add("trust_light", {"record": {"beat_epoch": rec.get("beat_epoch"), "registry_verified": ver},
                            "bind": bind, "now_ms": NOW_MS},
            tb.trust_light(rec, {"status": bind} if bind else None, NOW_MS))

def seat(**kw):
    s = {"schema": "aib-seat/v1", "seat_id": "s0001", "role": "dev", "provider": "claude", "trust": "self_reported"}
    s.update(kw)
    return {k: v for k, v in s.items() if v is not ...}

seats = [
    seat(), seat(schema="aib-seat/v0"), seat(seat_id=""), seat(seat_id=...), seat(role=...), seat(provider=...),
    seat(role=5), seat(provider="CLAUDE"), seat(provider="codex"), seat(advisor=True), seat(advisor=1),
    seat(captain_assigned=True), seat(captain_assigned=True, advisor=True), seat(role="root"),
    seat(role="root", provider=" DeepSeek "), seat(role="root", provider="nvidia"), seat(role="root", provider="openrouterjev"),
    seat(role="root", provider=...), seat(trust=...), seat(trust=""), seat(trust=True), seat(trust=3),
    seat(_label="@abcde"), seat(_label="@ab?de"), seat(_label="abcde"), seat(_label="@"), seat(role="開發"),
    "not a dict",
]
signals = [
    {},
    {"pid_alive": True, "now_s": NOW + 0.9},
    {"pid_alive": False, "transcript_age_s": 30.7, "now_s": NOW + 0.5},
    {"pid_alive": False, "transcript_age_s": -5.0, "now_s": NOW + 0.5},
    {"pid_alive": False, "transcript_age_s": 7200.0, "now_s": NOW},
    {"pid_alive": False, "now_s": NOW},
    {"persona": "小龍", "rank_hint": "👑", "model": "claude-opus-5[1m]", "now_s": NOW, "pid_alive": True},
    {"persona": "假@他", "rank_hint": "⭐", "model": "gpt-5.6-sol"},
    {"persona": "a|b", "rank_hint": "❓"}, {"persona": "", "rank_hint": "🔑", "model": ""},
    {"rank_hint": "🤖", "model": "claude-haiku-4-5-20251001"}, {"rank_hint": "🎓"}, {"rank_hint": "⚓"},
]
for st in seats:
    for sg in signals:
        rec = tb.seat_record(st, **sg)
        label = st.get("_label") if isinstance(st, dict) else None
        lights = [tb.seat_light(rec, n) for n in (NOW_MS, (NOW + 3600) * 1000, (NOW + 3601) * 1000 + 1)]
        add("seat_record", {"seat": st, "label": label, **sg}, {"rec": rec, "lights": lights})
add("seat_light", {"rec": None, "now_ms": NOW_MS}, tb.seat_light(None, NOW_MS))
add("seat_light", {"rec": {"sig": "", "beat_epoch": NOW}, "now_ms": NOW_MS}, tb.seat_light({"sig": "", "beat_epoch": NOW}, NOW_MS))

for inst in [None, "", "  ", "20261113-035938-dab9", "20261113-035938-ab", "main", "13", "a-b-cdefgh", "-", "x-", " 20261104-121013-f457 ", "無-艙號碼五字"]:
    add("cabin_label", {"instance": inst}, tb.cabin_label(inst))

for st, now in [(None, NOW), (0, NOW), (-5, NOW), (NOW + 10, NOW), (NOW, NOW), (NOW - 41.9, NOW + 0.2),
                (NOW - 7*60 - 3, NOW), (NOW - 12*3600 - 34*60 - 5, NOW), (NOW - 3*86400 - 4*3600, NOW),
                (NOW - 86400, NOW), (NOW - 3600, NOW), (NOW - 60, NOW), (1.5, 3.9)]:
    add("uptime_str", {"started": st, "now_s": now}, tb.uptime_str(st, now))

import time as _t
def lt(e):
    t = _t.gmtime(e)
    return t
for ep in [None, 0, -1, NOW, NOW + 0.99, 86400 * 45 + 3661]:
    for inj in [True, False]:
        tm = _t.gmtime(int(ep)) if (inj and ep is not None and int(ep) > 0) else None
        add("cabin_since", {"epoch": ep, "parts": [tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec] if tm else None, "inject": inj},
            tb.cabin_since(ep, lt if inj else None))

lights = [{"level": "red", "code": "no_seat_record", "detail": "d"}, {"level": "yellow", "code": "seat_live", "detail": "d"}, {"level": "green", "code": "verified_fresh", "detail": "d"}]
opts = [
    {}, {"role": "dev"}, {"role": ""}, {"cabin": "dab9"}, {"cabin": "dab9", "addr": "192.0.2.7"},
    {"cabin": "dab9", "addr": "192.0.2.7", "public": "198.51.100.9"}, {"public": "198.51.100.9"},
    {"cabin": "", "public": "198.51.100.9"}, {"since": "11/13 09:12:00"}, {"since": "11/13 09:12:00", "uptime": "3h4m"},
    {"since": "", "uptime": "3h4m", "role": "pm"}, {"version": "v0.42.1↑"}, {"since": "x", "version": "v1?"},
    {"role": "pm", "version": "v1", "cabin": "main", "addr": "", "public": ""},
]
for sig in [None, "👑 小龍@abcde/Claude/Opus5"]:
    for L in lights:
        for beat in [0, 1, 3, -1]:
            for color in [False, True]:
                for o in opts:
                    add("title_string", {"sig": sig, "light": L, "beat": beat, "color": color, **o},
                        tb.title_string(sig, L, beat, color=color, **o))

for m in [None, "", "  ", "claude-opus-5", "claude-fable-5", "claude-haiku-4-5-20251001", "claude-opus-5[1m]", "gpt-5.6-sol",
          "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "anthropic.claude-3-7-sonnet", "models/gemini-2.5-pro",
          "CLAUDE-OPUS-5", "o3", "4o-mini", "deepseek_v3", "grok-4.1-fast", "claude-", "[1m]", "x-..-1",
          "kimi-k2-20250711", "20250711", "qwen-2-5-.", " Claude-Opus-4-1 "]:
    add("model_label", {"model": m}, tb.model_label(m))

for v in [None, "", "  ", "0.42.1-alpha.1", "0.2.0-preview.5", " 1.2.3 ", "-x", "1.0"]:
    add("short_version", {"version": v}, tb.short_version(v))
    for b, c in itertools.product([None, True, False], repeat=2):
        add("version_segment", {"version": v, "behind": b, "changed": c}, tb.version_segment(v, b, c))
for b, c in itertools.product([None, True, False], repeat=2):
    add("version_state", {"behind": b, "changed": c}, tb.version_state(b, c))

sys.stdout.write("[\n" + ",\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for x in out) + "\n]\n")
```
