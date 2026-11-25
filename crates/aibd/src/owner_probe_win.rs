//! W-3 (PLAN-ap26-windows-first-c2f0b-1125 r2 §1; 3e53b READ-ap26-r2): which of the Windows
//! machines this is, read without asking anyone, and what the page says about it.
//!
//! - (a1) Windows Hello set up, the pairing key in hardware (a TPM): the strong path.
//! - (a2) Windows Hello set up, the pairing key in software (no TPM: Passport falls back to a
//!   software key with no anti-hammering) — not the strong path (3e53b READ-ap26-r2 L).
//! - (b) / (c) no Windows Hello: one class here. Whether the account has a Windows password
//!   cannot be read without trying a logon (which counts against the account lockout), and
//!   the page's next step is the same for both: set up a Hello PIN.
//!
//! Nothing here opens a window or makes a key. What a class may do is decided in `owner_ok`
//! (W-2: the device reports its strength); the class here is for the page and the probe line.
//! Until the W run on a real Hello laptop (W0) the reads are a first guess:
//! `class_is_final` stays false.

use serde_json::{json, Value};

/// What a Windows owner-check probe reads; none of it asks the owner.
pub trait WinDevice {
    /// This process is in the signed-in user's own desktop (the active console session).
    fn at_desk(&self) -> bool;
    /// The Windows Hello (Passport) key storage provider opens.
    fn hello_provider(&self) -> bool;
    /// Hello (a PIN, face or finger) is set up for this account; `None` while not readable.
    fn hello_enrolled(&self) -> Option<bool>;
    /// The pairing key's NCrypt implementation type has the hardware flag; `None` when there
    /// is no pairing key yet or it could not be read.
    fn key_in_hardware(&self) -> Option<bool>;
}

/// What this Windows machine is, for the page.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WinClass {
    /// Not the signed-in user's own desktop (session 0, ssh, remote desktop): nothing asks here.
    NotAtDesk,
    /// (a1) Hello set up, the pairing key in hardware.
    HelloHardware,
    /// (a2) Hello set up, the pairing key in software.
    HelloSoftware,
    /// Hello set up, no pairing key read yet (before `pair-key init`).
    HelloKeyUnknown,
    /// (b) / (c) no Windows Hello.
    NoHello,
    /// Could not tell.
    Unknown,
}

impl WinClass {
    pub fn word(self) -> &'static str {
        match self {
            WinClass::NotAtDesk => "not_at_desk",
            WinClass::HelloHardware => "hello_hardware",
            WinClass::HelloSoftware => "hello_software",
            WinClass::HelloKeyUnknown => "hello_key_unknown",
            WinClass::NoHello => "no_hello",
            WinClass::Unknown => "unknown",
        }
    }

    /// What the page says (W-7 shows it; aibd composes it, the page never does).
    pub fn words(self) -> &'static str {
        match self {
            WinClass::NotAtDesk => {
                "aibd 不在你登入的桌面上執行(遠端連線或服務帳號),這裡不能請你做本人驗證。請在這台電腦前登入後再試。"
            }
            WinClass::HelloHardware => "這台用 Windows Hello 確認是你本人。",
            WinClass::HelloSoftware => {
                "這台沒有 TPM,Windows Hello 的金鑰存在軟體裡:一般動作可以做，要存簽章和高風險的動作不開放。"
            }
            WinClass::HelloKeyUnknown => {
                "這台有 Windows Hello,還沒建配對金鑰。建好後才知道金鑰在不在硬體裡。"
            }
            WinClass::NoHello => {
                "這台還沒設 Windows Hello,只能做一般動作。設一個 PIN(不用指紋硬體):設定 → 帳戶 → 登入選項 → PIN。沒有 Windows 登入密碼的話，要先設密碼才能設 PIN。"
            }
            WinClass::Unknown => {
                "還不知道這台有沒有設 Windows Hello,先只開一般動作。還沒設的話，設一個 PIN(不用指紋硬體):設定 → 帳戶 → 登入選項 → PIN,設好再建配對金鑰。"
            }
        }
    }
}

/// The class of what `d` reads; the desk comes first, so a "yes" read from another session
/// is never taken.
pub fn win_class(d: &dyn WinDevice) -> WinClass {
    if !d.at_desk() {
        return WinClass::NotAtDesk;
    }
    if !d.hello_provider() {
        return WinClass::NoHello;
    }
    match (d.hello_enrolled(), d.key_in_hardware()) {
        (Some(false), _) => WinClass::NoHello,
        (None, _) => WinClass::Unknown,
        (Some(true), Some(true)) => WinClass::HelloHardware,
        (Some(true), Some(false)) => WinClass::HelloSoftware,
        (Some(true), None) => WinClass::HelloKeyUnknown,
    }
}

/// The one line `aibd ctl owner-check probe` prints on Windows.
pub fn win_probe(d: &dyn WinDevice) -> Value {
    let c = win_class(d);
    json!({
        "platform": "windows",
        "at_desk": d.at_desk(),
        "hello_provider": d.hello_provider(),
        "hello_enrolled": d.hello_enrolled(),
        "key_in_hardware": d.key_in_hardware(),
        "class": c.word(),
        "class_is_final": false,
        "words": c.words(),
    })
}

/// This Windows machine (W-3; reads filled in by S10-3b-2). Windows has no way to read "Hello
/// is set up" without a window, so (c2f0b REVIEW-wincls L1): a pairing key whose public key
/// reads back from the Passport provider says Hello is set up (a Passport key can only be made
/// then); no pairing key, or one that does not read back, is `None` — [`WinClass::Unknown`],
/// never the strong path. The key's hardware flag is read only for such a key; unread is
/// software.
#[cfg(windows)]
pub struct Win {
    at_desk: bool,
    provider: bool,
    enrolled: Option<bool>,
    hardware: Option<bool>,
}

#[cfg(windows)]
impl Win {
    pub fn read(data: &std::path::Path) -> Win {
        let at_desk = aib_sys::hello::interactive_session().is_ok();
        let provider = at_desk && aib_sys::hello_ncrypt::provider_ready().is_ok();
        let key = provider
            .then(|| crate::touch::load(&crate::touch::key_dir(data)).ok())
            .flatten()
            .filter(|k| aib_sys::hello_ncrypt::public_key(&k.blob).ok() == Some(k.public));
        let enrolled = key.as_ref().map(|_| true);
        let hardware = key.map(|k| crate::touch::WinHello::hardware_bound(&k.blob));
        Win {
            at_desk,
            provider,
            enrolled,
            hardware,
        }
    }
}

#[cfg(windows)]
impl WinDevice for Win {
    fn at_desk(&self) -> bool {
        self.at_desk
    }
    fn hello_provider(&self) -> bool {
        self.provider
    }
    fn hello_enrolled(&self) -> Option<bool> {
        self.enrolled
    }
    fn key_in_hardware(&self) -> Option<bool> {
        self.hardware
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fake {
        desk: bool,
        provider: bool,
        enrolled: Option<bool>,
        hw: Option<bool>,
    }

    impl WinDevice for Fake {
        fn at_desk(&self) -> bool {
            self.desk
        }
        fn hello_provider(&self) -> bool {
            self.provider
        }
        fn hello_enrolled(&self) -> Option<bool> {
            self.enrolled
        }
        fn key_in_hardware(&self) -> Option<bool> {
            self.hw
        }
    }

    fn at(desk: bool, provider: bool, enrolled: Option<bool>, hw: Option<bool>) -> WinClass {
        win_class(&Fake {
            desk,
            provider,
            enrolled,
            hw,
        })
    }

    #[test]
    fn only_a_hardware_key_with_hello_is_the_strong_class() {
        assert_eq!(
            at(true, true, Some(true), Some(true)),
            WinClass::HelloHardware
        );
        // 3e53b READ-ap26-r2 L: no TPM ⇒ a software Passport key, not the strong class
        assert_eq!(
            at(true, true, Some(true), Some(false)),
            WinClass::HelloSoftware
        );
        assert_eq!(at(true, true, Some(true), None), WinClass::HelloKeyUnknown);
    }

    #[test]
    fn not_at_the_desk_comes_before_anything_hello_says() {
        for (p, e, h) in [
            (true, Some(true), Some(true)),
            (false, None, None),
            (true, None, Some(true)),
        ] {
            assert_eq!(
                at(false, p, e, h),
                WinClass::NotAtDesk,
                "reason: {p} {e:?} {h:?}"
            );
        }
    }

    #[test]
    fn no_provider_or_not_set_up_is_no_hello_and_unreadable_is_unknown() {
        assert_eq!(at(true, false, Some(true), Some(true)), WinClass::NoHello);
        assert_eq!(at(true, true, Some(false), Some(true)), WinClass::NoHello);
        assert_eq!(at(true, true, None, Some(true)), WinClass::Unknown);
    }

    #[test]
    fn every_class_has_its_own_word_and_page_words() {
        let all = [
            WinClass::NotAtDesk,
            WinClass::HelloHardware,
            WinClass::HelloSoftware,
            WinClass::HelloKeyUnknown,
            WinClass::NoHello,
            WinClass::Unknown,
        ];
        let mut words: Vec<&str> = all.iter().map(|c| c.word()).collect();
        words.sort_unstable();
        words.dedup();
        assert_eq!(words.len(), all.len());
        for c in all {
            assert!(
                !c.words().is_empty() && !c.words().contains("老師"),
                "reason: {c:?}"
            );
        }
        assert!(WinClass::NoHello.words().contains("PIN"));
        // c2f0b REVIEW-wincls L1: a machine not paired yet reads Unknown, so its words carry
        // the PIN step too
        assert!(WinClass::Unknown.words().contains("PIN"));
        assert!(WinClass::HelloSoftware.words().contains("TPM"));
    }

    #[test]
    fn the_probe_line_says_what_it_read_and_is_not_final() {
        let v = win_probe(&Fake {
            desk: true,
            provider: true,
            enrolled: Some(true),
            hw: Some(false),
        });
        assert_eq!(v["platform"], "windows");
        assert_eq!(v["class"], "hello_software");
        assert_eq!(v["key_in_hardware"], false);
        assert_eq!(v["class_is_final"], false);
        let unread = win_probe(&Fake {
            desk: true,
            provider: true,
            enrolled: None,
            hw: None,
        });
        assert!(unread["hello_enrolled"].is_null());
        assert_eq!(unread["class"], "unknown");
    }
}
