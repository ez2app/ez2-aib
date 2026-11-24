//! Reading the answers of the checks. The caller fetched them (the system's curl from
//! outside, or the server's own reads); these functions only say what they mean.
//! Anything that cannot be read is "could not tell", never "fine".

use crate::plan::State;

/// The `max-age` of the **last** response in `curl -D -` output (redirects print several
/// header blocks; the last is the one served). None: no such header, or unreadable.
pub fn max_age(headers: &str) -> Option<u32> {
    let last = headers
        .split("\r\n\r\n")
        .flat_map(|b| b.split("\n\n"))
        .filter(|b| b.trim_start().starts_with("HTTP/"))
        .last()?;
    let mut found = None;
    for line in last.lines() {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        if !name.trim().eq_ignore_ascii_case("cache-control") {
            continue;
        }
        for part in value.split(',') {
            let part = part.trim();
            if let Some(n) = part
                .strip_prefix("max-age=")
                .or_else(|| part.strip_prefix("MAX-AGE="))
            {
                // two different max-age values: cannot tell which one counts
                let n: u32 = n.trim().parse().ok()?;
                if found.is_some_and(|f| f != n) {
                    return None;
                }
                found = Some(n);
            }
        }
    }
    found
}

/// The status code of the last response in `curl -D -` output.
pub fn status(headers: &str) -> Option<u16> {
    headers
        .lines()
        .rev()
        .find(|l| l.starts_with("HTTP/"))?
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}

/// The `/e2/*` scripts read from outside, through Cloudflare (step 2b): five minutes,
/// the engine's own value, not the zone's four hours.
pub fn e2_ttl_ok(headers: &str) -> bool {
    status(headers) == Some(200) && max_age(headers) == Some(300)
}

/// How many times the engine's tag is in a page (step 4 / 4b): exactly one is right,
/// two is a double count (the plugin's and a hand-pasted one), none is an old page.
pub fn engine_tags(html: &str) -> usize {
    html.matches("data-ez2-engine").count()
}

/// The page judged by its engine tags.
pub fn page_state(html: &str) -> State {
    match engine_tags(html) {
        1 => State::Done,
        0 => State::Todo,
        _ => State::Stop("engine_tag_twice".into()),
    }
}

/// The site script as Cloudflare serves it holds the AI loader (8b b: the 11-24 fault —
/// the engine was right, the cached script had no loader, the page had no AI).
pub fn has_ai_loader(js: &str) -> bool {
    js.contains("/c2.js?k=")
}

/// `__ez2-check?n=<n>` on the answer page's host: the engine answers the same `n`
/// (step 7 / 8). Anything else, or unreadable: not this engine.
pub fn check_echo(body: &str, n: &str) -> bool {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    v["ok"] == 1 && v["n"].as_str() == Some(n) && n.len() >= 16
}

/// `/api/ai` read back (8b, PREREAD M3): switched on, Claude, a key. All three, or not
/// ready.
pub fn ai_ready(body: &str) -> bool {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    v["enabled"] == 1 && v["provider"] == "claude" && v["claude_has_key"] == 1
}

/// The question list of one article (8b c), by how many questions it has (the caller
/// reads the count out of the engine's answer; F2 takes the answer's shape from a real
/// read, not from here): 3–5 is ready; fewer is still being made (wait); None (the
/// answer could not be read) or more than five: something else is wrong.
pub fn questions(count: Option<usize>) -> State {
    let Some(n) = count else {
        return State::Stop("questions_unreadable".into());
    };
    match n {
        3..=5 => State::Done,
        0..=2 => State::Wait("questions_being_made"),
        _ => State::Stop("questions_more_than_five".into()),
    }
}

/// Step 8b is ready only when all three hold (PREREAD Q2): the article page carries the
/// engine, the script Cloudflare serves holds the AI loader, and an article gets its
/// questions.
pub fn ai_questions_state(article_html: &str, cdn_script: &str, count: Option<usize>) -> State {
    if engine_tags(article_html) != 1 {
        return State::Todo;
    }
    if !has_ai_loader(cdn_script) {
        return State::Todo;
    }
    questions(count)
}

/// Real visitors' events in the last minutes (step 5, PREREAD M2): our own request
/// makes none (no script runs in curl), so none means waiting for visitors, not wrong.
pub fn events_state(recent: u64) -> State {
    if recent > 0 {
        State::Done
    } else {
        State::Wait("waiting_for_visitors")
    }
}

/// `ez2-cache-clear.sh` exit code (step 4b, PREREAD M4): 4 is "cooling down, try
/// again in 15 minutes", not a failure.
pub fn cache_clear_state(code: i32) -> State {
    match code {
        0 => State::Done,
        4 => State::Wait("cache_cooldown"),
        c => State::Stop(format!("cache_clear_exit_{c}")),
    }
}

/// LINE's webhook (step 6): LINE's own user agent posting without a signature gets the
/// plugin's 404; a 403 is a firewall rule eating LINE's events.
pub fn line_hook_state(code: Option<u16>) -> State {
    match code {
        Some(403) => State::Stop("line_webhook_blocked".into()),
        Some(_) => State::Warn("press_verify_in_line_developers"),
        None => State::Stop("line_webhook_unreadable".into()),
    }
}

/// The Windows curl (Schannel) could not check revocation — common behind company
/// networks. Shown as such (⚠️ with the reason), never answered by switching the check
/// off (PREREAD Q1).
pub fn revocation_unchecked(curl_stderr: &str) -> bool {
    curl_stderr.contains("CRYPT_E_NO_REVOCATION_CHECK")
        || curl_stderr.contains("CRYPT_E_REVOCATION_OFFLINE")
}

/// A tenant as the server's index lists it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tenant {
    pub id: String,
    pub slug: String,
    pub domains: Vec<String>,
}

/// Step 1 against what the server holds (PREREAD Q5): the slug and both names free ⇒
/// to do; already this shop's ⇒ done; a name another tenant has ⇒ stop, naming the
/// first 4 of that tenant's id; the slug held but without this domain ⇒ stop.
pub fn tenant_state(slug: &str, hosts: &[String], tenants: &[Tenant]) -> State {
    for t in tenants {
        if t.slug != slug && hosts.iter().any(|h| t.domains.contains(h)) {
            let short: String = t.id.chars().take(4).collect();
            return State::Stop(format!("domain_in_other_tenant:{short}"));
        }
    }
    match tenants.iter().find(|t| t.slug == slug) {
        None => State::Todo,
        Some(t) if hosts.iter().all(|h| t.domains.contains(h)) => State::Done,
        Some(t) if t.domains.is_empty() || hosts.iter().any(|h| t.domains.contains(h)) => {
            State::Todo
        }
        Some(_) => State::Stop("slug_taken_for_other_domain".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const E2_OK: &str = "HTTP/2 200 \r\ncontent-type: application/javascript\r\ncache-control: public, max-age=300\r\ncf-cache-status: HIT\r\n\r\n";
    const E2_ZONE: &str = "HTTP/2 200 \r\ncache-control: max-age=14400\r\n\r\n";

    #[test]
    fn the_e2_scripts_keep_the_engines_five_minutes() {
        assert!(e2_ttl_ok(E2_OK));
        assert!(!e2_ttl_ok(E2_ZONE), "reason: zone_four_hours_taken");
        let redirected = format!(
            "HTTP/1.1 301 Moved\r\ncache-control: max-age=14400\r\nlocation: x\r\n\r\n{E2_OK}"
        );
        assert!(e2_ttl_ok(&redirected), "reason: first_block_read");
        let other = "HTTP/2 404 \r\ncache-control: max-age=300\r\n\r\n";
        assert!(!e2_ttl_ok(other), "reason: not_found_passed");
        assert_eq!(
            max_age(
                "HTTP/2 200 \r\ncache-control: max-age=300\r\ncache-control: max-age=60\r\n\r\n"
            ),
            None
        );
        assert_eq!(
            max_age("HTTP/2 200 \nCache-Control: no-cache, max-age=300\n\n"),
            Some(300)
        );
        assert_eq!(max_age("garbage"), None);
    }

    #[test]
    fn a_page_carries_the_engine_once() {
        assert_eq!(page_state("<script data-ez2-engine src=x>"), State::Done);
        assert_eq!(page_state("<html></html>"), State::Todo);
        assert_eq!(
            page_state("<script data-ez2-engine></script><script data-ez2-engine></script>"),
            State::Stop("engine_tag_twice".into()),
            "reason: double_count_passed"
        );
    }

    #[test]
    fn the_ai_is_ready_only_with_all_three_read_back() {
        assert!(ai_ready(
            r#"{"enabled":1,"provider":"claude","claude_has_key":1}"#
        ));
        assert!(
            !ai_ready(r#"{"enabled":0,"provider":"claude","claude_has_key":1}"#),
            "reason: enabled_not_read"
        );
        assert!(
            !ai_ready(r#"{"enabled":1,"provider":"openai","claude_has_key":1}"#),
            "reason: provider_not_read"
        );
        assert!(
            !ai_ready(r#"{"enabled":1,"provider":"claude","claude_has_key":0}"#),
            "reason: key_not_read"
        );
        assert!(!ai_ready("not json"));
    }

    /// PREREAD Q2: the 11-24 fault was the cached script without the loader, with the
    /// engine fine.
    #[test]
    fn questions_count_only_with_the_engine_on_the_page_and_the_loader_in_the_cdn_script() {
        let page = "<script data-ez2-engine></script>";
        let js = "…import('/e2/c2.js?k=abc')…";
        let three = Some(3);
        assert_eq!(ai_questions_state(page, js, three), State::Done);
        assert_eq!(
            ai_questions_state(page, "old script", three),
            State::Todo,
            "reason: cdn_script_without_loader_passed"
        );
        assert_eq!(ai_questions_state("<p>", js, three), State::Todo);
        assert_eq!(
            ai_questions_state(page, js, Some(1)),
            State::Wait("questions_being_made")
        );
        assert_eq!(
            questions(Some(6)),
            State::Stop("questions_more_than_five".into())
        );
        assert_eq!(questions(None), State::Stop("questions_unreadable".into()));
    }

    #[test]
    fn the_answer_host_echoes_our_number() {
        let n = "0123456789abcdef";
        assert!(check_echo(r#"{"ok":1,"n":"0123456789abcdef","v":2}"#, n));
        assert!(
            !check_echo(r#"{"ok":1,"n":"fedcba9876543210"}"#, n),
            "reason: other_number_taken"
        );
        assert!(!check_echo(r#"{"ok":0,"n":"0123456789abcdef"}"#, n));
        assert!(!check_echo("<html>wordpress</html>", n));
        assert!(
            !check_echo(r#"{"ok":1,"n":"short"}"#, "short"),
            "reason: short_number_taken"
        );
    }

    /// PREREAD M2 / M4.
    #[test]
    fn no_visitors_and_a_cooling_cache_wait_and_are_not_failures() {
        assert_eq!(events_state(0), State::Wait("waiting_for_visitors"));
        assert_eq!(events_state(3), State::Done);
        assert_eq!(cache_clear_state(0), State::Done);
        assert_eq!(
            cache_clear_state(4),
            State::Wait("cache_cooldown"),
            "reason: cooldown_is_failure"
        );
        assert_eq!(
            cache_clear_state(30),
            State::Stop("cache_clear_exit_30".into())
        );
    }

    #[test]
    fn line_blocked_stops_and_line_answered_asks_a_person() {
        assert_eq!(
            line_hook_state(Some(403)),
            State::Stop("line_webhook_blocked".into())
        );
        assert_eq!(
            line_hook_state(Some(404)),
            State::Warn("press_verify_in_line_developers")
        );
        assert_eq!(
            line_hook_state(None),
            State::Stop("line_webhook_unreadable".into())
        );
        assert!(revocation_unchecked("curl: (35) schannel: next InitializeSecurityContext failed: Unknown error (0x80092012) - CRYPT_E_NO_REVOCATION_CHECK"));
        assert!(!revocation_unchecked("curl: (6) Could not resolve host"));
        assert_eq!(status("HTTP/1.1 301 x\nHTTP/2 404 \n"), Some(404));
    }

    /// PREREAD Q5: never take over a name or a slug someone else has.
    #[test]
    fn a_name_or_slug_another_tenant_has_stops_the_install() {
        let hosts = [
            "wuxuanbags.com".to_string(),
            "www.wuxuanbags.com".to_string(),
        ];
        let other = Tenant {
            id: "t_6z86zndd".into(),
            slug: "honewei".into(),
            domains: vec!["www.wuxuanbags.com".into()],
        };
        assert_eq!(
            tenant_state("wuxuan", &hosts, std::slice::from_ref(&other)),
            State::Stop("domain_in_other_tenant:t_6z".into()),
            "reason: other_tenants_domain_taken"
        );
        assert_eq!(tenant_state("wuxuan", &hosts, &[]), State::Todo);
        let mine = Tenant {
            id: "t_1".into(),
            slug: "wuxuan".into(),
            domains: hosts.to_vec(),
        };
        assert_eq!(
            tenant_state("wuxuan", &hosts, std::slice::from_ref(&mine)),
            State::Done
        );
        let half = Tenant {
            domains: vec!["wuxuanbags.com".into()],
            ..mine.clone()
        };
        assert_eq!(tenant_state("wuxuan", &hosts, &[half]), State::Todo);
        let elsewhere = Tenant {
            domains: vec!["other.com".into()],
            ..mine
        };
        assert_eq!(
            tenant_state("wuxuan", &hosts, &[elsewhere]),
            State::Stop("slug_taken_for_other_domain".into()),
            "reason: slug_taken_over"
        );
    }
}
