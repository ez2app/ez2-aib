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
}
