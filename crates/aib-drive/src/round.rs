//! `aib-drive round --seat <席>` (S79-own o-3, IFACE-s79-own-gate-a9136-1116 r1 §3): the
//! steps of one round for a seat, printed by the program so the timed prompt no longer
//! carries them as a long text. Read only: nothing is written, no ledger line.
//!
//! Step 0 is the seat's first item from [`crate::own::own_list`] with its evidence pattern;
//! then the inbox, the ledger line that ends the turn, the dispatch throttle (only for a seat
//! that sends), and the status line before handing back.
use crate::act::{SEAT_GAP_MIN, UNREAD_MAX};
use crate::own::OwnOut;

/// The round's text for one seat. `sends` = the seat dispatches to others (the coordinator,
/// or 自動找事做 running on its behalf); the throttle step is only printed then.
pub fn steps(o: &OwnOut, sends: bool) -> String {
    let seat = &o.seat;
    let mut s = format!("aib-drive round {seat}({} · {})\n", o.role, o.as_of);
    s.push_str("0. 能自己做的第 1 件:");
    match o.items.first() {
        Some(i) => s.push_str(&format!(
            "{} — {}\n   為什麼是你:{} · 為什麼是現在:{}\n   證據樣式:{}\n",
            i.id, i.what, i.why_you, i.why_now, i.evidence
        )),
        None if o.sources.get("plan").map(String::as_str) != Some("ok") => {
            s.push_str("進度樹讀不到(這輪不算怠惰;先查 cockpit-plan/data.json)\n")
        }
        None => {
            let why = o
                .skipped
                .iter()
                .take(3)
                .map(|k| format!("{} {}", k.id, k.reason))
                .collect::<Vec<_>>()
                .join(";");
            s.push_str(&format!(
                "沒有(清單空{})\n   記帳:aib-drive own none --seat {seat} --why \"<原因>\"\n",
                if why.is_empty() {
                    String::new()
                } else {
                    format!(":{why}")
                }
            ));
        }
    }
    s.push_str(&format!(
        "1. 收信:aib dm-queue list --seat {seat} --tab waiting;每封讀完 read --done \
         --evidence <檔|commit|PR>(純通知 --why);在等人先 read 再 wait\n"
    ));
    if let Some(i) = o.items.first() {
        s.push_str(&format!(
            "2. 做第 0 件,做完記帳:aib-drive own done --seat {seat} --id {} --evidence <type>:<ref>\n   \
             還沒做完:aib-drive own progress --seat {seat} --id {} --evidence <type>:<ref> --next \"<下一步>\"\n",
            i.id, i.id
        ));
    } else {
        s.push_str("2. (沒有第 0 件:記 none 就好)\n");
    }
    let mut n = 3;
    if sends {
        s.push_str(&format!(
            "{n}. 派工節流:在忙也發;同一席 {SEAT_GAP_MIN} 分鐘最多一封;未讀 ≥ {UNREAD_MAX} 或失聯不發;\
             寄件人署名「自動找事做」,信尾寫代發席碼\n"
        ));
        n += 1;
    }
    s.push_str(&format!(
        "{n}. 交回前:aib now \"<主項目>\" <做完>/<共> --state reply|decide --track N --why … --idea …\n"
    ));
    s
}
