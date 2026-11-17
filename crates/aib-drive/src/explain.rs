//! `aib-drive explain <席>`(S79-explain,a9136 11-17;遇事不決 herdr 定稿 r2 §2 借鏡
//! `herdr agent explain`):一席為什麼判成這樣、自動推進現在會不會寄信給它。
//!
//! 每個事實(alive / hands / waiting_on)照抄 [`seats::judge`] 的值與 why,再補三樣:
//! - `src`:這個值是從哪個來源讀的(seat-now / beat / window / dm-queue / lease),每個來源
//!   帶資料時間;讀不到的來源寫原因,不猜;
//! - `valid_until`:這個值最晚到什麼時候會自己變(例:alive yes 在最後動靜後
//!   [`LOST_HOURS`] 小時變 no;hands in_turn 在最後輸出後 [`HANDS_FREE_MIN`] 分鐘可能變
//!   free);沒有固定期限的寫 null 並在 `until` 說要等什麼;
//! - `until`:什麼事會讓它變。
//!
//! 寄信那關(`send`)照 act 的規則列:開關、這席上次收到自動推進的信(30 分一封,
//! [`SEAT_GAP_MIN`])、未讀上限([`UNREAD_MAX`])、alive。只讀:不寫 idle.jsonl、不發信、
//! 不碰意圖帳。狀態全是同帳號可寫的檔(GUIDE same-user):這裡是解釋,不是證據。

use crate::act::{self, History, Switches, SEAT_GAP_MIN, UNREAD_MAX};
use crate::seats::{
    self, iso, DmItem, FsSource, Known, Seat, SeatNow, Shared, Source, HANDS_FREE_MIN, LOST_HOURS,
};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// 輸出格式版本
pub const SCHEMA: &str = "aib-drive.explain.v1";

/// 寄信那關要讀的(讀不到就是 None,附原因)
#[derive(Debug, Clone, Default)]
pub struct Gate {
    /// `drive/act.json`
    pub switches: Option<Result<Switches, String>>,
    /// `drive/act-dryrun.jsonl` + `drive/intents.jsonl` 算出的歷史
    pub history: Option<Result<History, String>>,
}

/// 這一席在哪個艙哪個窗、是從哪裡知道的
fn place(
    shared: &Shared,
    dms: &Known<Vec<DmItem>>,
    seat: &str,
) -> Option<(String, String, &'static str)> {
    if let Known::Known(m) = &shared.wins {
        if let Some((h, w)) = m.get(seat) {
            return Some((h.clone(), w.clone(), "seat file"));
        }
    }
    match dms {
        Known::Known(d) => d
            .iter()
            .filter(|x| x.hub.is_some() && x.wid.is_some())
            .max_by_key(|x| x.sent_at)
            .and_then(|x| Some((x.hub.clone()?, x.wid.clone()?, "latest dm"))),
        Known::Unknown(_) => None,
    }
}

fn at(t: Option<u64>) -> Value {
    t.map_or(Value::Null, |t| json!(iso(t)))
}

/// 一個事實的 JSON:值、why、來源、期限
fn fact(
    name: &str,
    v: &str,
    why: &str,
    src: &[&str],
    valid_until: Option<u64>,
    until: &str,
) -> Value {
    json!({
        "name": name,
        "v": v,
        "why": why,
        "src": src,
        "valid_until": at(valid_until),
        "until": until,
    })
}

/// 一席的解釋(JSON)。`now` 秒。
pub fn explain(src: &dyn Source, seat: &str, now: u64, shared: &Shared, gate: &Gate) -> Value {
    let s: Seat = seats::judge(src, seat, now, shared);
    let sn = src.seat_now(seat);
    let beat = src.beat(seat);
    let dms = src.dms(seat);

    // ---- 來源 ----
    let mut sources = Vec::new();
    let sn_at = match &sn {
        Known::Known(SeatNow { at: t, state }) => {
            sources.push(json!({"name": "seat-now", "at": at(*t), "state": state}));
            *t
        }
        Known::Unknown(e) => {
            sources.push(json!({"name": "seat-now", "unknown": e}));
            None
        }
    };
    sources.push(json!({"name": "beat", "at": at(beat)}));
    let where_ = place(shared, &dms, seat);
    let activity: Known<Option<u64>> = match &where_ {
        None => Known::Unknown("no seat file entry and no dm with a cabin and window".into()),
        Some((hub, wid, _)) => match src.windows(hub) {
            Known::Known(w) => Known::Known(w.get(wid).copied()),
            Known::Unknown(e) => Known::Unknown(e),
        },
    };
    let act_at = match &activity {
        Known::Known(t) => *t,
        Known::Unknown(_) => None,
    };
    match (&where_, &activity) {
        (Some((hub, wid, from)), Known::Known(t)) => sources.push(json!({
            "name": "window", "hub": hub, "wid": wid, "from": from,
            "present": t.is_some(), "at": at(*t),
        })),
        (Some((hub, wid, from)), Known::Unknown(e)) => sources.push(json!({
            "name": "window", "hub": hub, "wid": wid, "from": from, "unknown": e,
        })),
        (None, _) => sources.push(json!({
            "name": "window", "unknown": "no seat file entry and no dm with a cabin and window",
        })),
    }
    let queued = match &dms {
        Known::Known(d) => {
            let q = d
                .iter()
                .any(|x| x.status == "queued" && x.done_at.is_none());
            let n = d.iter().map(|x| x.sent_at).max();
            sources.push(json!({
                "name": "dm-queue", "unread": s.unread, "queued": q, "newest_sent_at": at(n),
            }));
            q
        }
        Known::Unknown(e) => {
            sources.push(json!({"name": "dm-queue", "unknown": e}));
            false
        }
    };
    match &shared.lease {
        Known::Known(o) => sources.push(json!({"name": "lease", "owner": o})),
        Known::Unknown(e) => sources.push(json!({"name": "lease", "unknown": e})),
    }

    // ---- 事實 + 期限 ----
    let last = [beat, sn_at, act_at].into_iter().flatten().max();
    let alive = match s.alive.v {
        "yes" => fact(
            "alive",
            "yes",
            &s.alive.why,
            &["window", "dm-queue", "seat-now", "beat"],
            last.map(|t| t + LOST_HOURS * 3600),
            &format!("no window output, seat-now or beat for {LOST_HOURS} h ⇒ no"),
        ),
        "no" if s.alive.why == "window gone" => fact(
            "alive",
            "no",
            &s.alive.why,
            &["window"],
            None,
            "the window comes back",
        ),
        "no" if s.alive.why.starts_with("unread ") => fact(
            "alive",
            "no",
            &s.alive.why,
            &["dm-queue"],
            None,
            &format!("unread below {}", seats::UNREAD_LOST),
        ),
        v => fact(
            "alive",
            v,
            &s.alive.why,
            &["window", "dm-queue", "seat-now", "beat"],
            None,
            "new window output, seat-now or beat",
        ),
    };
    let hands = match s.hands.v {
        "in_turn" if queued => fact(
            "hands",
            "in_turn",
            &s.hands.why,
            &["dm-queue"],
            None,
            "the queued dm goes in",
        ),
        "in_turn" => fact(
            "hands",
            "in_turn",
            &s.hands.why,
            &["window"],
            act_at.map(|t| t + HANDS_FREE_MIN * 60),
            &format!("no window output for {HANDS_FREE_MIN} min ⇒ free"),
        ),
        "free" => fact(
            "hands",
            "free",
            &s.hands.why,
            &["window", "dm-queue"],
            None,
            "new window output or a dm queued ⇒ in_turn",
        ),
        v => fact(
            "hands",
            v,
            &s.hands.why,
            &["window"],
            None,
            "the window can be read",
        ),
    };
    let waiting_why = match s.waiting_on {
        "receipt" => "holds this machine's receipt lease".to_owned(),
        "human" => "seat-now state decide".to_owned(),
        "seat" => "seat-now state reply".to_owned(),
        "unknown" => "seat-now or lease unreadable".to_owned(),
        _ => "seat-now has no reply / decide state".to_owned(),
    };
    let waiting_src: &[&str] = if s.waiting_on == "receipt" {
        &["lease"]
    } else {
        &["seat-now", "lease"]
    };
    let waiting = fact(
        "waiting_on",
        s.waiting_on,
        &waiting_why,
        waiting_src,
        None,
        "the next aib now / the lease is released",
    );

    // ---- 寄信那關 ----
    let mut blockers: Vec<String> = Vec::new();
    let switches = match &gate.switches {
        Some(Ok(sw)) => {
            if sw.paused {
                blockers.push("paused".into());
            }
            sw.to_json()
        }
        Some(Err(e)) => {
            blockers.push(format!("switches unreadable: {e}"));
            json!({"unknown": e})
        }
        None => Value::Null,
    };
    let now_ms = (now as i64).saturating_mul(1000);
    let (last_ms, next_ok) = match &gate.history {
        Some(Ok(h)) => match h.last_sent_ms.get(seat) {
            Some(&l) => {
                let next = l + SEAT_GAP_MIN * 60_000;
                if now_ms < next {
                    blockers.push(format!(
                        "sent {} min ago (one per {SEAT_GAP_MIN} min)",
                        (now_ms - l) / 60_000
                    ));
                }
                (json!(act::rfc3339_ms(l)), json!(act::rfc3339_ms(next)))
            }
            None => (Value::Null, Value::Null),
        },
        Some(Err(e)) => {
            blockers.push(format!("journal unreadable: {e}"));
            (json!({"unknown": e}), Value::Null)
        }
        None => (Value::Null, Value::Null),
    };
    match s.unread {
        Some(u) if (u as u64) < UNREAD_MAX => {}
        Some(u) => blockers.push(format!("unread {u} (max {UNREAD_MAX})")),
        None => blockers.push("unread unknown".into()),
    }
    if s.alive.v != "yes" {
        blockers.push(format!("alive {}", s.alive.v));
    }
    if s.role != "dev" {
        blockers.push(format!("role {}", s.role));
    }

    json!({
        "schema": SCHEMA,
        "seat": seat,
        "now": iso(now),
        "cabin": s.cabin,
        "role": s.role,
        "verdict": {"dispatchable": s.dispatchable, "size": s.size, "reasons": s.reasons},
        "facts": [alive, hands, waiting],
        "sources": sources,
        "send": {
            "switches": switches,
            "last_sent": last_ms,
            "next_ok": next_ok,
            "unread": s.unread,
            "unread_max": UNREAD_MAX,
            "blockers": blockers,
        },
        "data_at": at(s.data_at),
    })
}

/// 文字版(給人看)
pub fn to_text(v: &Value) -> String {
    let g = |p: &str| {
        v.pointer(p)
            .and_then(Value::as_str)
            .unwrap_or("-")
            .to_owned()
    };
    let mut t = format!(
        "{} · {} · 艙 {} · 資料時間 {}\n",
        g("/seat"),
        g("/role"),
        g("/cabin"),
        g("/data_at")
    );
    for f in v["facts"].as_array().into_iter().flatten() {
        let s = |k: &str| f[k].as_str().unwrap_or("-").to_owned();
        let srcs: Vec<&str> = f["src"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect();
        t.push_str(&format!(
            "{:<10} {:<8} {}  來源 {} · 有效到 {} · 會變:{}\n",
            s("name"),
            s("v"),
            s("why"),
            srcs.join("/"),
            f["valid_until"].as_str().unwrap_or("(不定)"),
            s("until"),
        ));
    }
    for s in v["sources"].as_array().into_iter().flatten() {
        t.push_str(&format!("  來源 {}\n", s));
    }
    t.push_str(&format!(
        "派工判定 dispatchable={} size={}",
        g("/verdict/dispatchable"),
        g("/verdict/size")
    ));
    let reasons: Vec<&str> = v["verdict"]["reasons"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    if !reasons.is_empty() {
        t.push_str(&format!(":{}", reasons.join(";")));
    }
    t.push('\n');
    let b: Vec<&str> = v["send"]["blockers"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    t.push_str(&format!(
        "寄信關:開關 {} · 上次寄給這席 {} · 下次最早 {} · {}\n",
        v["send"]["switches"],
        v["send"]["last_sent"].as_str().unwrap_or("(沒有)"),
        v["send"]["next_ok"].as_str().unwrap_or("(現在)"),
        if b.is_empty() {
            "沒有擋的".to_owned()
        } else {
            format!("擋:{}", b.join("、"))
        }
    ));
    t
}

/// 讀 `<drive>/act.json`、`act-dryrun.jsonl`、`intents.jsonl`
pub fn read_gate(drive: &Path, now_ms: i64) -> Gate {
    let read = |p: PathBuf| match std::fs::read_to_string(&p) {
        Ok(t) => Ok(t),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(e) => Err(format!("{}: {e}", p.display())),
    };
    let history = read(drive.join("act-dryrun.jsonl"))
        .and_then(|j| History::parse_at(&j, now_ms))
        .and_then(|h| read(drive.join(crate::intents::FILE)).map(|l| h.with_ledger(&l)));
    Gate {
        switches: Some(act::read_switches(drive)),
        history: Some(history),
    }
}

/// `aib-drive explain <席> [--json] [--state-root DIR] [--plan FILE] [--repo DIR]`
pub fn run(args: &[String], now: u64) -> (i32, String) {
    let mut as_json = false;
    let mut seat: Option<String> = None;
    let mut root: Option<String> = None;
    let mut plan: Option<String> = None;
    let mut repo: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            "--json" => {
                as_json = true;
                continue;
            }
            "--state-root" => &mut root,
            "--plan" => &mut plan,
            "--repo" => &mut repo,
            s if !s.starts_with('-') && seat.is_none() => {
                seat = Some(s.to_owned());
                continue;
            }
            other => return (2, format!("unknown argument {other:?}\n")),
        };
        match it.next() {
            Some(v) => *slot = Some(v.clone()),
            None => return (2, format!("{a} needs a value\n")),
        }
    }
    let Some(seat) = seat.filter(|s| seats::is_seat_code(s)) else {
        return (2, "usage: aib-drive explain <5-hex seat> [--json] [--state-root DIR] [--plan FILE] [--repo DIR]\n".into());
    };
    let Some(root) = aib_expect::state_root(root.as_deref()) else {
        return (3, "no state root (HOME / USERPROFILE unset)\n".into());
    };
    let drive = root.join("drive");
    let mut src = FsSource::new(root);
    if let Some(p) = plan {
        src.plan = PathBuf::from(p);
    }
    src.repo = repo;
    let shared = Shared::read(&src, now);
    let gate = read_gate(&drive, (now as i64).saturating_mul(1000));
    let v = explain(&src, &seat, now, &shared, &gate);
    if as_json {
        (0, format!("{v}\n"))
    } else {
        (0, to_text(&v))
    }
}
