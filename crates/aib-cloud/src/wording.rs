// SPDX-License-Identifier: Apache-2.0
//! What a customer reads next to the codes the host answers with (zh-TW).
//!
//! Every sentence here is **final wording**, word for word from
//! `HUMAN-PASTE-4542e-1110.md` section F (Andy, 11-10). Do not rephrase one in a code
//! change: a new sentence goes through that file first. The host never sends these; it
//! sends codes, and the customer's side looks the sentence up here. A code or a check
//! this table does not know is **not shown** rather than shown raw. No I/O.

use alloc::borrow::ToOwned;
use alloc::string::String;

use serde_json::Value;

use crate::client::NotSent;
use crate::customer::{Refused, Request};

/// The name of a row of `status`, by check id.
pub fn row_name(id: &str) -> Option<&'static str> {
    Some(match id {
        "S01" => "網站運作",
        "S05" => "方案資源",
        "P" => "平台檢查",
        _ => return None,
    })
}

/// The sentence for one row of `status`, by its result and code.
pub fn row_text(result: &str, code: Option<&str>) -> Option<&'static str> {
    Some(match (result, code) {
        ("green", None) => "正常",
        (_, Some("site_down")) => {
            "網站目前沒有在運作。我們已收到通知並在處理;若您剛執行過重新啟動,請稍候一分鐘再查看。"
        }
        (_, Some("below_plan")) => {
            "目前配置的資源低於您的方案,我們會調整到方案內容。網站仍可正常使用。"
        }
        ("warn", Some("provider_check")) => {
            "平台這一側有一項需要留意,由我們處理,您不需要做任何事。"
        }
        ("red", Some("provider_check")) => {
            "平台這一側有一項正在處理中,由我們處理。若您的網站目前無法使用,請與您的服務窗口聯絡。"
        }
        (_, Some("unknown")) => "這一項暫時讀不到,請稍後再查看。",
        _ => return None,
    })
}

/// The one sentence on top of `status`, by the host's exit status.
pub fn overall(exit: u8) -> Option<&'static str> {
    Some(match exit {
        0 => "一切正常。",
        2 => "網站可正常使用,有一項由我們跟進。",
        1 => "有一項正在處理中。",
        3 => "部分狀態暫時讀不到,請稍後再試。",
        _ => return None,
    })
}

/// Above a log that has lines.
pub const LOGS_HEAD: &str = "以下是最近的記錄,密碼與金鑰類的內容已自動遮蔽。";
/// A log with no lines in the time asked for.
pub const LOGS_NONE: &str = "這段時間內沒有記錄。";
/// Below a log the host cut at its limit.
pub const LOGS_CUT: &str = "記錄較多,只顯示到上限為止。請縮短時間範圍或減少行數後再查看。";
/// The host answered `provider_check` instead of a log.
pub const LOGS_PROVIDER: &str = "目前無法提供記錄,由我們處理中。如需協助請與您的服務窗口聯絡。";
/// The log could not be read for another reason.
pub const LOGS_UNREAD: &str = "記錄暫時讀不到,請稍後再試。";
/// The host did not answer.
pub const NOT_REACHED: &str =
    "目前連不上平台。請確認您的網路連線後再試;若持續發生,請與您的服務窗口聯絡。";
/// There is no `ssh` program on this machine.
pub const NO_SSH: &str = "這台電腦找不到 ssh 程式,請先安裝 OpenSSH 後再試。";

/// The last screen of a site the customer opened (E1 N1), word for word from
/// `HUMAN-PASTE-4542e-1112.md` (final: Andy 11-12 04:03 「照草稿用，第二位管理員保留」). The
/// customer takes the one-time login link with the command named here themselves (Andy
/// 11-12 03:58: no mail, no platform seat running it for them). The link is never in this
/// text: it may be kept with a receipt (ebb36 L1).
///
/// `second` is the second administrator's login. Without one, the half sentence
/// 「(另一位:…)」 is left out whole and the line ends after the first login; nothing else
/// changes. An empty second login, or the first one again, is no second one (c477a N1,
/// N2). The site's code, the domain and the logins are made plain
/// ([`crate::svc::plain`]) first.
///
/// The command is this program's own, `aib-cloud site <code> login-link` (Andy 11-12
/// 04:3x: 「加 aib-cloud site login-link」): a customer key names a site by its code. The
/// draft's `aib cloud site login-link <網域>` was replaced by it, and nothing else
/// (HUMAN-PASTE-4542e-1112.md, 回填).
pub fn opened(code: &str, domain: &str, login: &str, second: Option<&str>) -> String {
    let code = crate::svc::plain(code);
    let domain = crate::svc::plain(domain);
    let second = second.filter(|s| !s.is_empty() && *s != login);
    let login = crate::svc::plain(login);
    let also = match second {
        Some(s) => format!("(另一位:{})", crate::svc::plain(s)),
        None => String::new(),
    };
    format!(
        "✅ 網站已開好:https://{domain}\n   管理員帳號:{login}{also}\n\n下一步:拿登入連結\n   請輸入:aib-cloud site {code} login-link\n   畫面會出現一條連結,點開就直接進後台。\n   ・連結 10 分鐘內有效,只能用一次。\n   ・過期或用掉了,再輸入同一行指令拿新的。\n   ・連結等於鑰匙,請不要轉傳或貼到公開的地方。\n\n進後台後,建議先到「使用者 → 個人資料」設定自己的密碼。"
    )
}

/// What fills sentences 17 to 21 in (a site's code, a domain), made plain
/// ([`crate::svc::plain`]) first: nothing in it acts on the terminal.
fn filled(word: &str) -> String {
    crate::svc::plain(word)
}

// Sentences 17 to 21 of `HUMAN-PASTE-4542e-1111.md` (sections 開站(補) and 放棄開站;
// final: Andy 11-12 12:1x 「照這版定稿」), word for word. Do not rephrase one here: a
// change goes through that file first.

/// Sentence 17: `site create` answered `opening`: the customer's opening has started.
pub fn opening(code: &str, domain: &str) -> String {
    let (code, domain) = (filled(code), filled(domain));
    format!(
        "已開始開新網站 {domain},站碼 {code}。大約需要幾分鐘,可以用「site {code} status」看進度。"
    )
}

/// Sentence 18: `site create` was refused `already_opening` and the opening is the customer's own:
/// the host gives its code only then. Anyone else's opening is [`DOMAIN_TAKEN`] (13).
pub fn already_opening(code: &str, domain: &str) -> String {
    let (code, domain) = (filled(code), filled(domain));
    format!("{domain} 已經在開站中(站碼 {code}),不需要重送;可以用「site {code} status」看進度。")
}

/// Sentences 13 and 19: `domain_taken`, and `already_opening` for someone else's opening (no code
/// of the customer's own in the answer). Sentence 19 is sentence 13; it is not written
/// twice.
pub const DOMAIN_TAKEN: &str = "這個網域已經綁在一個網站上,不能重複綁定。";

/// Sentence 20: a site just opened whose answer names no administrator to log in with: the login
/// comes separately. An answer naming one is the opened screen ([`opened`]).
pub fn opened_without_login(code: &str, domain: &str) -> String {
    let (code, domain) = (filled(code), filled(domain));
    format!("新網站已開好。站碼:{code} 網址:https://{domain} 管理員登入方式會另外提供給您。")
}

/// Sentence 21: `site <code> status` of an opening we abandoned (stopped, what it had made
/// cleared away; `why: abandoned`).
pub fn abandoned(domain: &str) -> String {
    let domain = filled(domain);
    format!("{domain} 這次開站沒有完成,我們已經把做到一半的部分清掉了。您可以重新開站;若需要協助,請與您的服務窗口聯絡。")
}

/// Why a request was not sent.
pub fn not_sent(why: NotSent) -> &'static str {
    match why {
        NotSent::Request(Refused::Empty) => "請輸入要查看的項目,例如:status、logs nginx。",
        NotSent::Request(Refused::UnknownRequest) => {
            "沒有這個項目。可用的有:status、logs(nginx / php / db)。"
        }
        NotSent::Request(Refused::BadArguments) => {
            "參數不正確。記錄的寫法是:logs <nginx|php|db> [lines 1–2000] [minutes 1–10080]。"
        }
        NotSent::Request(Refused::Malformed) => {
            "請求裡有不能使用的字元,只能用小寫英文、數字、連字號與空白。"
        }
        NotSent::BadHost | NotSent::BadUser | NotSent::BadPort | NotSent::BadPath => {
            "連線設定不正確,請確認我們提供給您的連線資料是否填寫完整。"
        }
    }
}

/// `status` as the customer reads it: the overall sentence, then one line per row this
/// table knows. `None` when the reply is not the document the host writes.
pub fn status(reply: &str) -> Option<String> {
    let v: Value = serde_json::from_str(reply.trim()).ok()?;
    let exit = u8::try_from(v.get("exit")?.as_u64()?).ok()?;
    let mut text = String::from(overall(exit)?);
    for check in v.get("checks")?.as_array()? {
        let word = |key: &str| check.get(key).and_then(Value::as_str);
        let name = word("id").and_then(row_name);
        let sentence = word("result").and_then(|r| row_text(r, word("code")));
        // a row or a code not named above is left out, never shown as it came
        if let (Some(name), Some(sentence)) = (name, sentence) {
            text.push('\n');
            text.push_str(name);
            text.push(':');
            text.push_str(sentence);
        }
    }
    Some(text)
}

/// The host's answer to `login-link`: the link alone, one line, for the customer to open
/// (Andy 11-12 03:58). Nothing else is added to it, and this side keeps it nowhere: no
/// file, no log (ebb36 L1). Anything but one `https://` line made of plain characters is
/// not shown as a link: the host's fixed word is shown instead (`refused: <word>` or the
/// word alone), or `unavailable` when it said none.
pub fn login_link(exit: u8, out: &str, err: &str) -> String {
    let lines: alloc::vec::Vec<&str> = out.lines().filter(|l| !l.trim().is_empty()).collect();
    if let [only] = lines.as_slice() {
        let only = only.trim();
        if exit == 0 && only.starts_with("https://") && crate::svc::plain(only) == only {
            return only.to_owned();
        }
    }
    err.lines()
        .map(str::trim)
        .map(|l| l.strip_prefix("refused: ").unwrap_or(l))
        .find(|w| !w.is_empty() && w.bytes().all(|c| c.is_ascii_lowercase() || c == b'_'))
        .unwrap_or("unavailable")
        .to_owned()
}

/// What the customer is shown for the host's answer to `request`: its exit status, what
/// it wrote and what it said on the side (`provider_check`, `output cut at the limit`).
pub fn reply(request: Request, exit: u8, out: &str, err: &str) -> String {
    let said = |word: &str| err.lines().any(|l| l.trim() == word);
    match request {
        Request::Status => {
            status(out).unwrap_or_else(|| "部分狀態暫時讀不到,請稍後再試。".to_owned())
        }
        Request::Logs { .. } => {
            if said("provider_check") {
                LOGS_PROVIDER.to_owned()
            } else if exit != 0 {
                LOGS_UNREAD.to_owned()
            } else if out.trim().is_empty() {
                LOGS_NONE.to_owned()
            } else {
                let mut text = String::from(LOGS_HEAD);
                text.push('\n');
                // once more on this side: an older host, or a line that came some other
                // way, still reaches the terminal as plain text (ebb36, #648 L2)
                let lines: alloc::vec::Vec<String> = out
                    .trim_end_matches('\n')
                    .lines()
                    .map(crate::svc::plain)
                    .collect();
                text.push_str(&lines.join("\n"));
                if said("output cut at the limit") {
                    text.push('\n');
                    text.push_str(LOGS_CUT);
                }
                text
            }
        }
        Request::LoginLink => login_link(exit, out, err),
        // not offered yet: never sent, so there is no answer to word
        Request::PhpReload | Request::Restart | Request::Backup => String::new(),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use crate::customer::parse_request;

    #[test]
    fn status_reads_as_sentences_and_shows_nothing_it_does_not_know() {
        let quiet = r#"{"site":"site2","exit":0,"checks":[
            {"id":"S01","result":"green","code":null},
            {"id":"S05","result":"green","code":null},
            {"id":"P","result":"green","code":null}]}"#;
        assert_eq!(
            status(quiet).unwrap(),
            "一切正常。\n網站運作:正常\n方案資源:正常\n平台檢查:正常"
        );
        let busy = r#"{"site":"site2","exit":1,"checks":[
            {"id":"S01","result":"red","code":"site_down"},
            {"id":"S05","result":"warn","code":"below_plan"},
            {"id":"P","result":"red","code":"provider_check"}]}"#;
        let text = status(busy).unwrap();
        assert!(text.starts_with("有一項正在處理中。\n網站運作:網站目前沒有在運作。"));
        assert!(text.contains("\n方案資源:目前配置的資源低於您的方案,"));
        assert!(text.contains("\n平台檢查:平台這一側有一項正在處理中,由我們處理。"));
        // the two provider sentences differ by result
        assert_ne!(
            row_text("warn", Some("provider_check")),
            row_text("red", Some("provider_check"))
        );
        assert_eq!(
            row_text("unknown", Some("unknown")),
            Some("這一項暫時讀不到,請稍後再查看。")
        );
        // a row, a code or a result this table does not know is left out, not shown raw
        let odd = r#"{"site":"site2","exit":2,"checks":[
            {"id":"S99","result":"warn","code":"new_thing"},
            {"id":"S01","result":"red","code":"fw_disabled"},
            {"id":"P","result":"warn","code":"provider_check"}]}"#;
        let text = status(odd).unwrap();
        assert_eq!(
            text,
            "網站可正常使用,有一項由我們跟進。\n平台檢查:平台這一側有一項需要留意,由我們處理,您不需要做任何事。"
        );
        // not the host's document: nothing is made up
        for bad in [
            "",
            "provider_check",
            "{}",
            r#"{"exit":9,"checks":[]}"#,
            r#"{"exit":0}"#,
        ] {
            assert_eq!(status(bad), None, "{bad}");
        }
    }

    #[test]
    fn every_answer_has_its_sentence() {
        let logs = parse_request("logs nginx").unwrap();
        assert_eq!(
            reply(logs, 0, "line 1\nline 2\n", ""),
            "以下是最近的記錄,密碼與金鑰類的內容已自動遮蔽。\nline 1\nline 2"
        );
        assert_eq!(
            reply(logs, 0, "line 1\n", "output cut at the limit\n"),
            "以下是最近的記錄,密碼與金鑰類的內容已自動遮蔽。\nline 1\n記錄較多,只顯示到上限為止。請縮短時間範圍或減少行數後再查看。"
        );
        assert_eq!(reply(logs, 0, "\n", ""), "這段時間內沒有記錄。");
        // the neutral word wins over everything else, and says nothing of the reason
        assert_eq!(reply(logs, 3, "", "provider_check\n"), LOGS_PROVIDER);
        assert_eq!(reply(logs, 3, "", "could not read the log\n"), LOGS_UNREAD);
        // a log line that merely contains the word is a log line
        assert!(reply(logs, 0, "GET /provider_check 200\n", "").starts_with(LOGS_HEAD));
        // a line the host did not make plain is made plain here
        let shown = reply(logs, 0, "a\u{1b}[2Jb\nc\u{e0041}d\n", "");
        assert!(shown.ends_with("a\\x1b[2Jb\nc\\u{e0041}d"), "{shown}");
        // status that is not the host's document reads as "could not be read"
        assert_eq!(
            reply(Request::Status, 3, "", "could not read the site record\n"),
            "部分狀態暫時讀不到,請稍後再試。"
        );
        assert_eq!(
            not_sent(NotSent::Request(Refused::UnknownRequest)),
            "沒有這個項目。可用的有:status、logs(nginx / php / db)。"
        );
        assert_eq!(not_sent(NotSent::BadHost), not_sent(NotSent::BadPath));
        // nothing here names what is not to be named
        for sentence in [LOGS_PROVIDER, LOGS_UNREAD, NOT_REACHED, NO_SSH] {
            for word in ["隔離", "防火牆", "錯誤", "故障"] {
                assert!(!sentence.contains(word), "{sentence}");
            }
        }
    }

    /// E1 N1: the opened site's last screen, word for word as HUMAN-PASTE-4542e-1112.md
    /// has it (Andy 11-12 04:03); only the domain and the logins are filled in.
    #[test]
    fn the_opened_screen_is_the_final_wording_word_for_word() {
        assert_eq!(
            opened("s-abc123", "shop.example.test", "ops", Some("ops2")),
            "✅ 網站已開好:https://shop.example.test
   管理員帳號:ops(另一位:ops2)

下一步:拿登入連結
   請輸入:aib-cloud site s-abc123 login-link
   畫面會出現一條連結,點開就直接進後台。
   ・連結 10 分鐘內有效,只能用一次。
   ・過期或用掉了,再輸入同一行指令拿新的。
   ・連結等於鑰匙,請不要轉傳或貼到公開的地方。

進後台後,建議先到「使用者 → 個人資料」設定自己的密碼。"
        );
        // one administrator: the half sentence goes whole, nothing else changes
        assert_eq!(
            opened("s-abc123", "shop.example.test", "ops", None),
            "✅ 網站已開好:https://shop.example.test
   管理員帳號:ops

下一步:拿登入連結
   請輸入:aib-cloud site s-abc123 login-link
   畫面會出現一條連結,點開就直接進後台。
   ・連結 10 分鐘內有效,只能用一次。
   ・過期或用掉了,再輸入同一行指令拿新的。
   ・連結等於鑰匙,請不要轉傳或貼到公開的地方。

進後台後,建議先到「使用者 → 個人資料」設定自己的密碼。"
        );
    }

    /// What fills the screen in is made plain: nothing in it acts on the terminal.
    #[test]
    fn the_opened_screen_shows_nothing_that_acts_on_the_terminal() {
        let text = opened(
            "s\u{1b}c",
            "a\u{1b}[2Jb.example.test",
            "o\u{7}ps",
            Some("x\u{202e}y"),
        );
        assert!(!text.chars().any(|c| c.is_control() && c != '\n'), "{text}");
        assert!(!text.contains('\u{202e}'), "{text}");
        assert!(text.contains("https://a\\x1b[2Jb.example.test\n"), "{text}");
        assert!(
            text.contains("aib-cloud site s\\x1bc login-link\n"),
            "{text}"
        );
        assert!(text.contains(":o\\x07ps("), "{text}");
    }

    /// Sentences 17 to 21: what fills them in is made plain, nothing acts on the terminal.
    /// (Their words are pinned whole by `aib-cloud-cli`'s tests, where they are shown.)
    #[test]
    fn the_opening_sentences_show_nothing_that_acts_on_the_terminal() {
        let (code, domain) = ("s\u{1b}c", "a\u{1b}[2Jb\u{202e}.example.test");
        for text in [
            opening(code, domain),
            already_opening(code, domain),
            opened_without_login(code, domain),
            abandoned(domain),
        ] {
            assert!(!text.chars().any(char::is_control), "{text}");
            assert!(!text.contains('\u{202e}'), "{text}");
            assert!(text.contains("a\\x1b[2Jb"), "{text}");
        }
        assert!(opening(code, domain).contains("site s\\x1bc status"));
    }

    /// c477a N1 / N2: an empty second login, or the first one again, is no second one.
    #[test]
    fn a_second_administrator_that_is_not_another_one_is_left_out() {
        let one = opened("s-abc123", "shop.example.test", "ops", None);
        assert_eq!(
            opened("s-abc123", "shop.example.test", "ops", Some("")),
            one
        );
        assert_eq!(
            opened("s-abc123", "shop.example.test", "ops", Some("ops")),
            one
        );
        assert!(opened("s-abc123", "shop.example.test", "ops", Some("ops2"))
            .contains("ops(另一位:ops2)\n"));
    }

    /// The link is shown as the host gave it, alone; anything else is the host's word.
    #[test]
    fn a_login_link_is_shown_alone_or_not_at_all() {
        let url = "https://shop.example.test/?ez2_otl=AbC123";
        let ll = parse_request("login-link").unwrap();
        assert_eq!(
            reply(
                ll,
                0,
                &format!("{url}\n"),
                "login link issued once for ops\n"
            ),
            url
        );
        // more than the link, or not a link: never shown as one
        assert_eq!(reply(ll, 0, &format!("{url}\n{url}\n"), ""), "unavailable");
        assert_eq!(
            reply(ll, 0, "http://shop.example.test/?x\n", ""),
            "unavailable"
        );
        assert_eq!(reply(ll, 0, "https://a\u{1b}[2J\n", ""), "unavailable");
        // a refusal or a failure is the host's fixed word
        assert_eq!(
            reply(ll, 2, "", "refused: role_forbidden\n"),
            "role_forbidden"
        );
        assert_eq!(
            reply(ll, 2, "", "refused: tenant_mismatch\n"),
            "tenant_mismatch"
        );
        assert_eq!(reply(ll, 3, "", "provider_check\n"), "provider_check");
        assert_eq!(
            reply(ll, 3, &format!("{url}\n"), "unavailable\n"),
            "unavailable"
        );
        assert_eq!(reply(ll, 3, "", "could not read\n"), "unavailable");
    }
}
