//! `aibd ctl seat move <old seat code>` (SPEC-seat-open-oneline-519d0-1121; Andy 11-21 10:4x
//! 「開席的過程中,把開席的流程也做成一行開席的指令」): what moving a terminal seat into the web
//! takes, found from the seat code alone — its persona (`identity/bindings.json`), its newest
//! handoff and memory pack in the pending folder, and the repo its handoff names (`倉庫:`).
//!
//! This module only finds and plans ([`plan`], [`steps`]); nothing is opened here. The old seat
//! is never closed (closing a seat is the person's red line): the plan says so.
use std::path::{Path, PathBuf};

/// Where moving `code` starts from.
#[derive(Debug, PartialEq, Eq)]
pub struct Plan {
    pub code: String,
    pub persona_uid: String,
    pub provider: String,
    pub model: Option<String>,
    /// The handoff's file name in the pending folder (`HANDOFF-<code>-….md`).
    pub handoff: String,
    /// The memory pack's (`MEMPACK-<code>-<4 digits>.md`), when there is one.
    pub mempack: Option<String>,
    /// The repo: `--repo`, else the handoff's `倉庫:` line; absolute.
    pub repo: Option<String>,
}

/// A seat code: 5 lowercase hex.
pub fn code_ok(code: &str) -> bool {
    code.len() == 5
        && code
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The persona `code` is bound to in `bindings.json` (`{"<code>": {"persona_uid": …}}`).
pub fn persona_of(bindings: &str, code: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(bindings).ok()?;
    let uid = v.get(code)?.get("persona_uid")?.as_str()?;
    (!uid.is_empty()).then(|| uid.to_owned())
}

/// The newest of `names` that is `<prefix><code>-….md` (the page's own shape for a memory
/// pack: `MEMPACK-<code>-<4 digits>.md`, so `…-answers.md` is not one), by the name's sort
/// order and then the modified time given.
pub fn newest(
    names: &[(String, std::time::SystemTime)],
    prefix: &str,
    code: &str,
) -> Option<String> {
    let head = format!("{prefix}{code}-");
    names
        .iter()
        .filter(|(n, _)| {
            let Some(rest) = n.strip_prefix(&head).and_then(|r| r.strip_suffix(".md")) else {
                return false;
            };
            if prefix == crate::seat_carry::MEMPACK {
                rest.len() == 4 && rest.bytes().all(|b| b.is_ascii_digit())
            } else {
                crate::seat_carry::name_ok(n, prefix)
            }
        })
        .max_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(&b.0)))
        .map(|(n, _)| n.clone())
}

/// The repo a handoff names: the first `倉庫:` line's absolute path.
pub fn repo_of(handoff: &str) -> Option<String> {
    handoff.lines().find_map(|l| {
        let l = l.trim_start_matches(|c: char| c == '-' || c == '*' || c.is_whitespace());
        let rest = l
            .strip_prefix("倉庫:")
            .or_else(|| l.strip_prefix("倉庫\u{ff1a}"))?
            .trim();
        Path::new(rest).is_absolute().then(|| rest.to_owned())
    })
}

/// The plan for moving `code`: `identity` is the folder holding `bindings.json`, `pending` the
/// pending folder ([`crate::docs::root`]).
pub fn plan(
    code: &str,
    identity: &Path,
    pending: &Path,
    provider: Option<&str>,
    model: Option<&str>,
    repo: Option<&str>,
) -> Result<Plan, String> {
    if !code_ok(code) {
        return Err(format!("「{code}」不是席碼(5 碼小寫十六進位)"));
    }
    let b = std::fs::read_to_string(identity.join("bindings.json"))
        .map_err(|e| format!("讀不到 bindings.json:{e}"))?;
    let persona_uid =
        persona_of(&b, code).ok_or_else(|| format!("bindings.json 沒有 {code} 的人格"))?;
    let names: Vec<(String, std::time::SystemTime)> = std::fs::read_dir(pending)
        .map_err(|e| format!("讀不到交接夾 {}:{e}", pending.display()))?
        .filter_map(|e| {
            let e = e.ok()?;
            let m = e.metadata().ok()?;
            if !m.is_file() {
                return None;
            }
            Some((
                e.file_name().to_string_lossy().into_owned(),
                m.modified().ok()?,
            ))
        })
        .collect();
    let handoff = newest(&names, crate::seat_carry::HANDOFF, code)
        .ok_or_else(|| format!("交接夾沒有 HANDOFF-{code}-….md;先在舊席跑 aib handoff"))?;
    let mempack = newest(&names, crate::seat_carry::MEMPACK, code);
    let repo = match repo {
        Some(r) if Path::new(r).is_absolute() => Some(r.to_owned()),
        Some(r) => return Err(format!("--repo 要絕對路徑:{r}")),
        None => {
            let text = crate::seat_carry::read(pending, &handoff, crate::seat_carry::HANDOFF)
                .map_err(|e| format!("交接檔讀不了({e}):{handoff}"))?;
            repo_of(&String::from_utf8_lossy(&text))
        }
    };
    let provider = provider.unwrap_or("claude");
    if !matches!(provider, "claude" | "codex") {
        return Err(format!("--provider 只收 claude / codex:{provider}"));
    }
    Ok(Plan {
        code: code.to_owned(),
        persona_uid,
        provider: provider.to_owned(),
        model: model.map(str::to_owned),
        handoff,
        mempack,
        repo,
    })
}

/// The six steps the move takes, one line each (what `--dry-run` prints).
pub fn steps(p: &Plan) -> Vec<String> {
    let repo = p
        .repo
        .as_deref()
        .unwrap_or("(交接檔沒寫倉庫:做事席開在自己的空資料夾)");
    let mp = p
        .mempack
        .as_deref()
        .map(|m| format!(" + {m}"))
        .unwrap_or_default();
    vec![
        format!(
            "1. 檢查:aibd 在跑、{} 已登入(沒登入就到網頁按「重新登入 AI」)",
            p.provider
        ),
        format!("2. 倉:{repo} 沒登記就登記(會跳一次指紋窗)"),
        format!(
            "3. 開做事席:人格 {}、{}、{}、帶倉副本",
            p.persona_uid,
            p.provider,
            p.model.as_deref().unwrap_or("預設模型")
        ),
        format!("4. 拷 {}{mp} 進新席資料夾的 .aib/(唯讀、不進 git)", p.handoff),
        "5. 送第一句:讀 .aib/ 兩份,回「我是誰、手上哪一件、下一步」;409 延後重試最多 3 次、間隔 10 秒".to_owned(),
        format!(
            "6. 印結果:新席碼、席資料夾、第一句已送 / 失敗原因;舊席 {} 不關(關席要真人)",
            p.code
        ),
    ]
}

/// `<home>/.ez2-aib/identity`, home as [`crate::personas::default_cards_path`] picks it.
pub fn identity_dir(lookup: impl Fn(&str) -> Option<String>) -> Option<PathBuf> {
    crate::personas::default_cards_path(lookup)?
        .parent()
        .map(Path::to_path_buf)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use std::time::{Duration, SystemTime};

    /// MV1: the persona comes from the seat code's binding; a code is 5 hex.
    #[test]
    fn the_persona_is_the_seat_codes_binding() {
        let b = r#"{"a9136": {"persona_uid": "un0eba", "route_key": "x"}, "6f1cb": {"persona_uid": ""}}"#;
        assert_eq!(persona_of(b, "a9136").as_deref(), Some("un0eba"));
        assert_eq!(persona_of(b, "6f1cb"), None, "reason: empty_uid_taken");
        assert_eq!(persona_of(b, "b4297"), None);
        assert!(code_ok("a9136"));
        for bad in ["A9136", "a913", "a91366", "a913g", "../a9"] {
            assert!(!code_ok(bad), "reason: code_shape {bad}");
        }
    }

    /// MV2: the newest handoff / memory pack of that seat only; `…-answers.md` is not a pack.
    #[test]
    fn the_newest_handoff_and_pack_of_that_seat_are_taken() {
        let t = |s| SystemTime::UNIX_EPOCH + Duration::from_secs(s);
        let names = vec![
            ("HANDOFF-a9136-1120-2237.md".to_owned(), t(10)),
            ("HANDOFF-a9136-1121-0910.md".to_owned(), t(20)),
            ("HANDOFF-a91360-1121-0910.md".to_owned(), t(30)),
            ("HANDOFF-6f1cb-1121-0001.md".to_owned(), t(40)),
            ("MEMPACK-a9136-1121.md".to_owned(), t(5)),
            ("MEMPACK-a9136-1121-answers.md".to_owned(), t(50)),
        ];
        assert_eq!(
            newest(&names, crate::seat_carry::HANDOFF, "a9136").as_deref(),
            Some("HANDOFF-a9136-1121-0910.md"),
            "reason: not_newest_or_other_seat"
        );
        assert_eq!(
            newest(&names, crate::seat_carry::MEMPACK, "a9136").as_deref(),
            Some("MEMPACK-a9136-1121.md"),
            "reason: answers_taken_as_pack"
        );
        assert_eq!(newest(&names, crate::seat_carry::MEMPACK, "6f1cb"), None);
    }

    /// MV3: the repo is the handoff's `倉庫:` line, absolute only.
    #[test]
    fn the_repo_is_the_handoffs_repo_line() {
        assert_eq!(
            repo_of("# x\n- 倉庫: /Users/a/ez2-aib\n- 倉庫: /other\n").as_deref(),
            Some("/Users/a/ez2-aib")
        );
        assert_eq!(repo_of("倉庫\u{ff1a}/r\n").as_deref(), Some("/r"));
        assert_eq!(
            repo_of("- 倉庫: relative/path\n"),
            None,
            "reason: relative_repo"
        );
        assert_eq!(repo_of("沒寫\n"), None);
    }

    /// MV4: a whole plan from the folders, and its six steps say the old seat stays open.
    #[test]
    fn a_plan_is_found_from_the_seat_code_alone() {
        let d = tempfile::tempdir().unwrap();
        let (id, pend) = (d.path().join("identity"), d.path().join("pending"));
        std::fs::create_dir_all(&id).unwrap();
        std::fs::create_dir_all(&pend).unwrap();
        std::fs::write(
            id.join("bindings.json"),
            r#"{"6f1cb": {"persona_uid": "5s3bot"}}"#,
        )
        .unwrap();
        std::fs::write(pend.join("HANDOFF-6f1cb-1121-1030.md"), "- 倉庫: /r/ez2\n").unwrap();
        std::fs::write(pend.join("MEMPACK-6f1cb-1121.md"), "m").unwrap();
        let p = plan("6f1cb", &id, &pend, None, None, None).unwrap();
        assert_eq!(p.persona_uid, "5s3bot");
        assert_eq!(p.handoff, "HANDOFF-6f1cb-1121-1030.md");
        assert_eq!(p.mempack.as_deref(), Some("MEMPACK-6f1cb-1121.md"));
        assert_eq!(p.repo.as_deref(), Some("/r/ez2"));
        assert_eq!(p.provider, "claude");
        let s = steps(&p);
        assert_eq!(s.len(), 6);
        assert!(s[3].contains("HANDOFF-6f1cb-1121-1030.md + MEMPACK-6f1cb-1121.md"));
        assert!(s[5].contains("舊席 6f1cb 不關"), "reason: old_seat_closed");
        let r = plan("6f1cb", &id, &pend, None, None, Some("/x/y")).unwrap();
        assert_eq!(r.repo.as_deref(), Some("/x/y"));
        assert!(plan("6f1cb", &id, &pend, None, None, Some("rel")).is_err());
        assert!(plan("6f1cb", &id, &pend, Some("grok"), None, None).is_err());
        assert!(
            plan("a9136", &id, &pend, None, None, None).is_err(),
            "reason: unbound_taken"
        );
        std::fs::remove_file(pend.join("HANDOFF-6f1cb-1121-1030.md")).unwrap();
        assert!(
            plan("6f1cb", &id, &pend, None, None, None)
                .unwrap_err()
                .contains("aib handoff"),
            "reason: no_handoff_taken"
        );
    }
}
