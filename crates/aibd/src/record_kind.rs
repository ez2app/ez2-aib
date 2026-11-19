//! A record's kinds on the card window (SPEC §15; 519d0 r16 ruling 2: one rule set, in aibd).
//!
//! The same rules the flow board's generator has used (stations-gen.js `kindsOf`, cfg.js
//! `onlyYes` / `humanQuoteRx`), with the same words as board-config.json: the person's own
//! words (need), the person agreeing (yes), fingerprint (bio), AI council (cc), summary,
//! hand-over (ho), test machines (host), clean-up (del); anything else is dev. One record may
//! have several. `AIBD_BOARD_CONFIG` names a board-config.json whose `kind_keywords`,
//! `human_names`, `consent_*` and `hosts[].name` replace the built-in words.

use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

/// The built-in words: board-config.json as of 11-18 (no machine names: those come from the
/// file, never from the code).
pub const DEFAULT_CONFIG: &str = r#"{
  "human_names": ["Andy"],
  "kind_keywords": {
    "bio": "Touch ID|指紋|生物辨識|consent ask|SIGN-[A-Za-z0-9]",
    "council": "遇事不決|DECISION-council",
    "summary": "總結|回顧|彙整|SUMMARY-|REVIEW24H-|DEEPREVIEW-",
    "handoff": "交接|HANDOFF-|HANDOVER-|TAKEOVER-",
    "host": "RECEIPT-|收據機|租約|自架 runner|ssh ",
    "clean": "刪除|刪掉|刪了|已刪|清掉|清除|清理|清了|worktree remove|封存|騰出",
    "yes_after_human": "同意|准了|放行|回「"
  },
  "consent_prefix": "同意|非常好|很好|好的?|可以|對|是的?|OK|ok|做|動手|照(?:你的?)?建議|准|沒問題|就這樣",
  "consent_tail": "同意|可以|照做"
}"#;

/// The kinds, in the board's order (its tab order).
pub const KINDS: &[&str] = &[
    "need", "yes", "bio", "cc", "sum", "ho", "drv", "shd", "host", "del", "dev",
];

pub struct Rules {
    yes_mark: Option<Regex>,
    yes_after: Option<Regex>,
    quote: Option<Regex>,
    bio: Option<Regex>,
    cc: Option<Regex>,
    sum: Option<Regex>,
    ho: Option<Regex>,
    host: Option<Regex>,
    del: Option<Regex>,
    c_pre: Option<Regex>,
    c_ask: Option<Regex>,
    c_tail: Option<Regex>,
}

fn rx(src: Option<String>) -> Option<Regex> {
    src.filter(|s| !s.is_empty())
        .and_then(|s| Regex::new(&s).ok())
}

fn s_of(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

impl Rules {
    /// Rules from a board-config shaped value (a missing word turns its kind off, as the
    /// generator does; no person named ⇒ no need / yes, never a guess).
    pub fn from_config(c: &Value) -> Rules {
        let kw = c.get("kind_keywords").cloned().unwrap_or(Value::Null);
        let names: Vec<String> = c
            .get("human_names")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .filter(|n| !n.is_empty())
                    .map(regex::escape)
                    .collect()
            })
            .unwrap_or_default();
        let human = (!names.is_empty()).then(|| names.join("|"));
        let hosts: Vec<String> = c
            .get("hosts")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|h| h.get("name").and_then(Value::as_str))
                    .filter(|n| !n.is_empty() && *n != "這台")
                    .map(regex::escape)
                    .collect()
            })
            .unwrap_or_default();
        let host = {
            let mut parts: Vec<String> = s_of(&kw, "host").into_iter().collect();
            parts.extend(hosts);
            (!parts.is_empty()).then(|| parts.join("|"))
        };
        Rules {
            yes_mark: rx(human.as_ref().map(|h| format!("(?:{h}) 同意:「"))),
            yes_after: rx(human.as_ref().and_then(|h| {
                s_of(&kw, "yes_after_human").map(|y| format!("(?:{h})[^。;;「」]{{0,16}}({y})"))
            })),
            quote: rx(human
                .as_ref()
                .map(|h| format!("(?:{h})[^「」\\n]{{0,40}}「([^」]{{4,}})」"))),
            bio: rx(s_of(&kw, "bio")),
            cc: rx(s_of(&kw, "council")),
            sum: rx(s_of(&kw, "summary")),
            ho: rx(s_of(&kw, "handoff")),
            host: rx(host),
            del: rx(s_of(&kw, "clean")),
            c_pre: rx(s_of(c, "consent_prefix").map(|p| {
                format!("^(?:[0-9一二三四五六七八九十]{{0,3}}\\s*(?:件|項|則)?\\s*(?:都|全部|全)?\\s*)?({p})")
            })),
            c_ask: rx(s_of(c, "consent_ask")),
            c_tail: rx(s_of(c, "consent_tail").map(|t| format!("({t})$"))),
        }
    }

    /// A quoted line that only says yes (cfg.js `onlyYes`).
    pub fn only_yes(&self, q: &str) -> bool {
        let Some(pre) = &self.c_pre else {
            return false;
        };
        let t = q.trim();
        let Some(m) = pre.captures(t) else {
            return self
                .c_tail
                .as_ref()
                .is_some_and(|r| t.chars().count() <= 10 && r.is_match(t));
        };
        let (Some(all), Some(word)) = (m.get(0), m.get(1)) else {
            return false;
        };
        let rest = t[all.end()..]
            .trim_start_matches(|c: char| c.is_whitespace() || ",,、。.!!::;;".contains(c));
        let strong = ["同意", "准", "照"]
            .iter()
            .any(|w| word.as_str().starts_with(w));
        if !strong && rest.chars().count() >= 8 {
            return false;
        }
        !(self
            .c_ask
            .as_ref()
            .is_some_and(|a| a.is_match(rest) && rest.chars().count() >= 6))
    }

    /// The kinds of one record (stations-gen.js `kindsOf`): never empty.
    pub fn kinds(&self, e: &str) -> Vec<&'static str> {
        let mut k = Vec::new();
        let hit = |r: &Option<Regex>| r.as_ref().is_some_and(|r| r.is_match(e));
        let mut need = 0;
        let mut yes = usize::from(hit(&self.yes_mark));
        if yes == 0 {
            if let Some(q) = &self.quote {
                for c in q.captures_iter(e) {
                    if c.get(1).is_some_and(|w| self.only_yes(w.as_str())) {
                        yes += 1;
                    } else {
                        need += 1;
                    }
                }
            }
        }
        if need > 0 {
            k.push("need");
        }
        if yes > 0 || hit(&self.yes_after) {
            k.push("yes");
        }
        for (r, name) in [
            (&self.bio, "bio"),
            (&self.cc, "cc"),
            (&self.sum, "sum"),
            (&self.ho, "ho"),
            (&self.host, "host"),
            (&self.del, "del"),
        ] {
            if hit(r) {
                k.push(name);
            }
        }
        if k.is_empty() {
            k.push("dev");
        }
        k
    }
}

/// This aibd's rules: `AIBD_BOARD_CONFIG` when it reads, else [`DEFAULT_CONFIG`].
pub fn rules() -> &'static Rules {
    static R: OnceLock<Rules> = OnceLock::new();
    R.get_or_init(|| {
        let from_file = std::env::var_os("AIBD_BOARD_CONFIG")
            .and_then(|p| std::fs::read(p).ok())
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok());
        let c = from_file
            .unwrap_or_else(|| serde_json::from_str(DEFAULT_CONFIG).unwrap_or(Value::Null));
        Rules::from_config(&c)
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn r() -> Rules {
        Rules::from_config(&serde_json::from_str(DEFAULT_CONFIG).unwrap())
    }

    #[test]
    fn the_built_in_words_compile() {
        let r = r();
        assert!(r.quote.is_some() && r.c_pre.is_some() && r.sum.is_some() && r.del.is_some());
    }

    #[test]
    fn the_persons_words_are_need_and_a_plain_yes_is_yes() {
        let r = r();
        assert_eq!(
            r.kinds("11-18 09:2x Andy 親打「無條件,先把這個畫面做出來」"),
            vec!["need"]
        );
        assert_eq!(
            r.kinds("11-18 Andy 回「同意」"),
            vec!["yes"],
            "reason: plain_yes_as_need"
        );
        assert_eq!(r.kinds("11-18 Andy 同意:「全照建議」(出處:x)"), vec!["yes"]);
        assert!(r.kinds("11-18 Andy 看過後放行").contains(&"yes"));
        assert_eq!(
            r.kinds("Andy 說「全部照建議」"),
            vec!["yes"],
            "reason: quoted_yes_as_need"
        );
    }

    #[test]
    fn keywords_mark_summary_hand_over_host_clean_and_the_rest_is_dev() {
        let r = r();
        assert_eq!(r.kinds("寫 DEEPREVIEW-7b81e-r16-1118.md"), vec!["sum"]);
        assert_eq!(r.kinds("HANDOFF-ws3-7b81e-1118.md 交 b4297"), vec!["ho"]);
        assert_eq!(r.kinds("RECEIPT-andymini-x.out 綠"), vec!["host"]);
        assert_eq!(r.kinds("worktree remove 舊樹"), vec!["del"]);
        assert_eq!(r.kinds("Touch ID 按了"), vec!["bio"]);
        assert_eq!(r.kinds("DECISION-council-x 定稿"), vec!["cc"]);
        assert_eq!(r.kinds("加 GET hosts 與刀"), vec!["dev"]);
        assert_eq!(r.kinds("交接 + 總結"), vec!["sum", "ho"]);
    }

    #[test]
    fn only_yes_takes_short_agreements_not_requests() {
        let r = r();
        assert!(r.only_yes("同意"));
        assert!(r.only_yes("三件都同意"));
        assert!(r.only_yes("照建議"));
        assert!(r.only_yes("好"));
        assert!(
            !r.only_yes("好,但是首頁要改成藍色並且加一個按鈕"),
            "reason: request_as_yes"
        );
        assert!(!r.only_yes("先把這個畫面做出來"));
    }

    #[test]
    fn no_person_named_means_no_need_or_yes() {
        let c = serde_json::json!({"kind_keywords": {"summary": "總結"}});
        let r = Rules::from_config(&c);
        assert_eq!(r.kinds("Andy 親打「無條件做出來」"), vec!["dev"]);
        assert_eq!(r.kinds("總結"), vec!["sum"]);
    }

    #[test]
    fn a_machine_name_from_the_file_marks_host() {
        let c = serde_json::json!({"kind_keywords": {}, "hosts": [{"name": "這台"}, {"name": "nick-1"}]});
        let r = Rules::from_config(&c);
        assert_eq!(r.kinds("nick-1 跑完"), vec!["host"]);
        assert_eq!(
            r.kinds("這台跑完"),
            vec!["dev"],
            "reason: this_machine_as_host_word"
        );
    }
}
