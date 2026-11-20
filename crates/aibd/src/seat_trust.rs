//! T1 (DESIGN-trust-t1-7b81e-1120, 5d33a BASELINE-trusted-seat §3 T1): the owner's fingerprint
//! makes one seat trusted for [`crate::trusted_seats::HOURS`] hours; a page takes it back.
//!
//! `POST /api/v1/act {"type":"seat_trust","seat_id":…,"ticket"?}` from the built-in page in
//! this pairing only (the caller refused a user folder page, a carried pairing, a seat's token).
//! The shape of every owner ask (IFACE-owner-touch r1): without a ticket aibd reads the facts,
//! passes [`gate`] and asks the fingerprint (`202 {ticket}`); with the ticket it reads the
//! facts again and writes only when the ticket approves this very reading, and then once more
//! under the write ([`commit`]: 5d33a PREREAD-trust-t1 L1 L2) — the switch, the page, the
//! seat, its mode, its repo's remote and the machine list are what they were when the owner's
//! finger answered. A seat trusted now is not asked again and nothing is written (`already`).
//!
//! `{"type":"seat_untrust","seat_id":…}` needs no fingerprint: it only makes things stricter.
//! Journals `seat.trusted {seat, until, by}` / `seat.untrusted {seat, by}`: no path, no remote.
use std::path::Path;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::guard::request_id;

pub const TRUST: &str = "seat_trust";
pub const UNTRUST: &str = "seat_untrust";
/// The operation the owner approves.
pub const OP: &str = "seat.trust";

/// What aibd reads about a seat before it asks, and again before it writes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Facts {
    pub switch_on: bool,
    pub user_page: bool,
    /// The seat's row state (`None`: no such seat).
    pub state: Option<String>,
    pub mode: Option<String>,
    /// The end of the trust it has now, if any.
    pub until: Option<u64>,
    /// The real remote of the registered repo its folder is a copy of (`""`: no repo).
    pub remote: String,
    /// [`crate::trusted_hosts::digest`] of the machine list (`""`: no list).
    pub hosts_digest: String,
}

/// The remote of the registered repo `cwd` is a copy of, as `repos.json` holds it now.
fn remote_of(data_dir: &Path, cwd: &str) -> String {
    crate::seat_worktree::base_of(Path::new(cwd))
        .and_then(|(_, author)| {
            let name = author.strip_prefix(crate::repos::REPO_AUTHOR_PREFIX)?;
            crate::repos::find(data_dir, name)
        })
        .map(|r| r.remote)
        .unwrap_or_default()
}

fn hosts_digest(data_dir: &Path) -> String {
    let p = data_dir
        .join(crate::trusted_hosts::DIR)
        .join(crate::trusted_hosts::FILE);
    crate::trusted_hosts::load(&p)
        .map(|(_, d)| d)
        .unwrap_or_default()
}

/// Read now; nothing is kept between two reads.
pub fn read(store: &aib_store::Store, data_dir: &Path, seat_id: &str) -> Facts {
    let meta = store.seat_meta(seat_id).ok().flatten();
    Facts {
        switch_on: crate::bridge_cfg::load(data_dir).trusted_seats,
        user_page: crate::static_files::web_source() == "user",
        state: store.seat(seat_id).ok().flatten().map(|s| s.state),
        mode: meta.as_ref().and_then(|m| m.mode.clone()),
        until: crate::trusted_seats::until(data_dir, seat_id),
        remote: meta
            .as_ref()
            .map(|m| remote_of(data_dir, &m.cwd))
            .unwrap_or_default(),
        hosts_digest: hosts_digest(data_dir),
    }
}

/// May this seat be made trusted? `(status, reason)` of the refusal.
pub fn gate(f: &Facts) -> Result<(), (StatusCode, &'static str)> {
    if !f.switch_on {
        return Err((StatusCode::FORBIDDEN, "trusted_seats_off"));
    }
    if f.user_page {
        return Err((StatusCode::FORBIDDEN, "page_only"));
    }
    match f.state.as_deref() {
        None => return Err((StatusCode::NOT_FOUND, "seat_not_found")),
        Some("ARCHIVED") => return Err((StatusCode::NOT_FOUND, "seat_archived")),
        Some(_) => {}
    }
    if !matches!(f.mode.as_deref(), Some("safe" | "work")) {
        return Err((StatusCode::FORBIDDEN, "seat_trust_mode"));
    }
    Ok(())
}

/// The seat's code as the window shows it: the five after `seat_`.
fn code(seat_id: &str) -> String {
    seat_id
        .strip_prefix("seat_")
        .unwrap_or(seat_id)
        .chars()
        .take(5)
        .collect()
}

/// What the owner is asked. The window is aibd's words only: the seat's code and mode, the
/// hours, where the repo's remote is, the machine list's digest; never a name a seat or a page
/// can set.
pub fn ask_of(seat_id: &str, f: &Facts, by: String) -> crate::owner_ok::Ask {
    let params = json!({
        "seat_id": seat_id,
        "hours": crate::trusted_seats::HOURS,
        "mode": f.mode,
        "remote": f.remote,
        "hosts_digest": f.hosts_digest,
    });
    let repo = if f.remote.is_empty() {
        "沒有倉".to_owned()
    } else {
        // the host's first 30: the seat's code ahead of it is never pushed out of the window
        let host: String = crate::repos::remote_host(&f.remote)
            .chars()
            .take(30)
            .collect();
        format!("remote {host}")
    };
    let hosts = if f.hosts_digest.is_empty() {
        "無".to_owned()
    } else {
        f.hosts_digest.chars().take(12).collect()
    };
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: format!(
            "{} {} 受信任 {} 小時 · {repo} · 測試機名單 {hosts}",
            code(seat_id),
            f.mode.as_deref().unwrap_or("?"),
            crate::trusted_seats::HOURS,
        ),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// After the fingerprint, on facts read again: the gate is passed again and what the owner
/// approved (`approved`: the ask's parameters) is still what the facts give (5d33a
/// PREREAD-trust-t1 L1 L2).
pub fn recheck(
    seat_id: &str,
    f: &Facts,
    approved: &[u8],
) -> Result<(), (StatusCode, &'static str)> {
    gate(f)?;
    if ask_of(seat_id, f, String::new()).params != approved {
        return Err((StatusCode::CONFLICT, "seat_trust_changed"));
    }
    Ok(())
}

/// The write: the facts are read once more, [`recheck`]ed, and only then is the trust written.
/// The end written.
pub fn commit(
    store: &aib_store::Store,
    data_dir: &Path,
    seat_id: &str,
    approved: &[u8],
    now: u64,
) -> Result<u64, (StatusCode, &'static str)> {
    recheck(seat_id, &read(store, data_dir, seat_id), approved)?;
    let until = now + crate::trusted_seats::HOURS * 3600;
    crate::trusted_seats::put(data_dir, seat_id, until, now)
        .map_err(|why| (StatusCode::INTERNAL_SERVER_ERROR, why))?;
    Ok(until)
}

fn refuse(status: StatusCode, reason: &str) -> Response {
    let message = match reason {
        "trusted_seats_off" => "trusted seats are off on this machine (trusted_seats in bridge.json)",
        "page_only" => "a seat is made trusted from the built-in page only",
        "seat_not_found" | "seat_archived" => "no such seat now",
        "seat_trust_mode" => "only a safe or work seat can be trusted",
        "seat_trust_changed" => "the seat, its repo's remote or the machine list changed since the fingerprint: ask again",
        "seat_id" => "body must be {type, seat_id, ticket?}; seat_id is a seat's id",
        _ => "the trust could not be written",
    };
    let code = match status {
        StatusCode::BAD_REQUEST => "invalid_request",
        StatusCode::NOT_FOUND => "not_found",
        StatusCode::CONFLICT => "conflict",
        StatusCode::INTERNAL_SERVER_ERROR => "internal",
        _ => "forbidden",
    };
    (
        status,
        Json(json!({"code": code, "reason": reason, "message": message,
            "request_id": request_id()})),
    )
        .into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    #[serde(rename = "type")]
    _kind: String,
    seat_id: String,
    #[serde(default)]
    ticket: Option<String>,
}

/// A seat id as aibd makes them: `seat_` and 1–64 of `a-z 0-9 _`.
pub fn seat_id_ok(s: &str) -> bool {
    s.strip_prefix("seat_").is_some_and(|r| {
        !r.is_empty()
            && r.len() <= 64
            && r.bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
    })
}

fn parse(body: Value) -> Option<Raw> {
    serde_json::from_value::<Raw>(body)
        .ok()
        .filter(|r| seat_id_ok(&r.seat_id))
}

/// `seat_trust`, for the built-in page `by` (`page:<pair>`).
pub async fn trust(st: AppState, body: Value, by: String) -> Response {
    let Some(req) = parse(body) else {
        return refuse(StatusCode::BAD_REQUEST, "seat_id");
    };
    let Some(data) = crate::bridge_cfg::dir() else {
        return refuse(StatusCode::FORBIDDEN, "trusted_seats_off");
    };
    let (store, d, sid) = (st.store.clone(), data.clone(), req.seat_id.clone());
    let Ok(f) = tokio::task::spawn_blocking(move || read(&store, &d, &sid)).await else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            crate::trusted_seats::STORE,
        );
    };
    if let Err((status, why)) = gate(&f) {
        return refuse(status, why);
    }
    let now = crate::pair_carry::now_unix();
    // trusted now: not asked again, and nothing is written (the end is not moved)
    if let Some(until) = f
        .until
        .filter(|u| crate::trusted_seats::holds(Some(*u), now))
    {
        return Json(
            json!({"done": true, "already": true, "seat_id": req.seat_id,
            "trusted_until": until}),
        )
        .into_response();
    }
    let ask = ask_of(&req.seat_id, &f, by.clone());
    let Some(ticket) = req.ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let (store, sid, approved) = (st.store.clone(), req.seat_id.clone(), ask.params.clone());
    let wrote = tokio::task::spawn_blocking(move || {
        commit(
            &store,
            &data,
            &sid,
            &approved,
            crate::pair_carry::now_unix(),
        )
    })
    .await;
    let until = match wrote {
        Ok(Ok(u)) => u,
        Ok(Err((status, why))) => return refuse(status, why),
        Err(_) => {
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                crate::trusted_seats::STORE,
            )
        }
    };
    crate::seats::side_event(
        &st,
        "seat_trust",
        "seat.trusted",
        json!({"seat": req.seat_id, "until": until, "by": by}),
    )
    .await;
    Json(json!({"done": true, "seat_id": req.seat_id, "trusted_until": until})).into_response()
}

/// `seat_untrust`, for the built-in page `by`: no fingerprint.
pub async fn untrust(st: AppState, body: Value, by: String) -> Response {
    let Some(req) = parse(body).filter(|r| r.ticket.is_none()) else {
        return refuse(StatusCode::BAD_REQUEST, "seat_id");
    };
    let Some(data) = crate::bridge_cfg::dir() else {
        return refuse(StatusCode::FORBIDDEN, "trusted_seats_off");
    };
    let sid = req.seat_id.clone();
    let took = tokio::task::spawn_blocking(move || {
        crate::trusted_seats::take(&data, &sid, crate::pair_carry::now_unix())
    })
    .await;
    let was = match took {
        Ok(Ok(w)) => w,
        _ => {
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                crate::trusted_seats::STORE,
            )
        }
    };
    if was {
        crate::seats::side_event(
            &st,
            "seat_trust",
            "seat.untrusted",
            json!({"seat": req.seat_id, "by": by}),
        )
        .await;
    }
    Json(json!({"done": true, "seat_id": req.seat_id, "was_trusted": was})).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts() -> Facts {
        Facts {
            switch_on: true,
            user_page: false,
            state: Some("ACTIVE".into()),
            mode: Some("work".into()),
            until: None,
            remote: "git@example.com:o/r.git".into(),
            hosts_digest: "0123456789abcdef0123".into(),
        }
    }

    #[test]
    fn only_a_live_safe_or_work_seat_is_asked_for_and_only_with_the_switch() {
        assert_eq!(gate(&facts()), Ok(()));
        let with = |edit: fn(&mut Facts)| {
            let mut f = facts();
            edit(&mut f);
            gate(&f).map_err(|e| e.1)
        };
        assert_eq!(with(|f| f.mode = Some("safe".into())), Ok(()));
        assert_eq!(
            with(|f| f.switch_on = false),
            Err("trusted_seats_off"),
            "reason: asked_with_switch_off"
        );
        assert_eq!(
            with(|f| f.user_page = true),
            Err("page_only"),
            "reason: asked_under_user_page"
        );
        assert_eq!(
            with(|f| f.state = None),
            Err("seat_not_found"),
            "reason: asked_for_no_seat"
        );
        assert_eq!(
            with(|f| f.state = Some("ARCHIVED".into())),
            Err("seat_archived"),
            "reason: asked_for_an_archived_seat"
        );
        assert_eq!(
            with(|f| f.mode = Some("dgrs".into())),
            Err("seat_trust_mode"),
            "reason: dgrs_seat_trusted"
        );
        assert_eq!(
            with(|f| f.mode = None),
            Err("seat_trust_mode"),
            "reason: unknown_mode_trusted"
        );
    }

    #[test]
    fn the_ticket_binds_the_seat_its_mode_its_remote_and_the_machine_list() {
        let a = ask_of("seat_abcde12345", &facts(), "page:1".into());
        assert_eq!(a.operation, OP);
        assert!(crate::owner_ok::page_category(a.category));
        let differs = |seat: &str, edit: fn(&mut Facts)| {
            let mut f = facts();
            edit(&mut f);
            ask_of(seat, &f, "page:1".into()).params != a.params
        };
        assert!(!differs("seat_abcde12345", |_| {}));
        assert!(differs("seat_zzzzz12345", |_| {}), "reason: seat_not_bound");
        assert!(
            differs("seat_abcde12345", |f| f.remote =
                "git@evil.example:o/r.git".into()),
            "reason: remote_not_bound"
        );
        assert!(
            differs("seat_abcde12345", |f| f.hosts_digest = "ffff".into()),
            "reason: machine_list_not_bound"
        );
        assert!(
            differs("seat_abcde12345", |f| f.mode = Some("safe".into())),
            "reason: mode_not_bound"
        );
        // facts that are not the owner's to approve do not move the ticket
        assert!(!differs("seat_abcde12345", |f| f.until = Some(5)));
    }

    #[test]
    fn the_window_is_aibds_words_with_the_key_facts_first() {
        let a = ask_of("seat_abcde12345", &facts(), "page:1".into());
        assert_eq!(
            a.resource,
            "abcde work 受信任 12 小時 · remote example.com · 測試機名單 0123456789ab"
        );
        assert!(
            a.resource.chars().count() <= crate::owner_ok::RESOURCE_MAX,
            "reason: window_text_cut"
        );
        let mut f = facts();
        f.remote = String::new();
        f.hosts_digest = String::new();
        let mut long = facts();
        long.remote = format!("git@{}.example.com:o/r.git", "h".repeat(200));
        let shown = ask_of("seat_abcde12345", &long, "page:1".into()).resource;
        assert!(
            shown.starts_with("abcde work ")
                && shown.chars().count() <= crate::owner_ok::RESOURCE_MAX,
            "reason: long_remote_pushed_the_code_out {shown}"
        );
        assert_eq!(
            ask_of("seat_abcde12345", &f, "page:1".into()).resource,
            "abcde work 受信任 12 小時 · 沒有倉 · 測試機名單 無"
        );
    }

    /// 5d33a PREREAD-trust-t1 L1 L2: between the fingerprint and the write the switch went off,
    /// a user page took over, the seat was archived or changed mode, the remote or the machine
    /// list changed: nothing is written.
    #[test]
    fn what_changed_after_the_fingerprint_stops_the_write() {
        let approved = ask_of("seat_abcde12345", &facts(), "page:1".into()).params;
        assert_eq!(recheck("seat_abcde12345", &facts(), &approved), Ok(()));
        let after = |edit: fn(&mut Facts)| {
            let mut f = facts();
            edit(&mut f);
            recheck("seat_abcde12345", &f, &approved).map_err(|e| e.1)
        };
        assert_eq!(
            after(|f| f.switch_on = false),
            Err("trusted_seats_off"),
            "reason: wrote_with_switch_off"
        );
        assert_eq!(
            after(|f| f.user_page = true),
            Err("page_only"),
            "reason: wrote_under_user_page"
        );
        assert_eq!(
            after(|f| f.state = Some("ARCHIVED".into())),
            Err("seat_archived"),
            "reason: wrote_for_an_archived_seat"
        );
        assert_eq!(
            after(|f| f.mode = Some("dgrs".into())),
            Err("seat_trust_mode"),
            "reason: wrote_for_a_dgrs_seat"
        );
        assert_eq!(
            after(|f| f.remote = "git@evil.example:o/r.git".into()),
            Err("seat_trust_changed"),
            "reason: wrote_after_remote_changed"
        );
        assert_eq!(
            after(|f| f.hosts_digest = String::new()),
            Err("seat_trust_changed"),
            "reason: wrote_after_machine_list_changed"
        );
        assert_eq!(
            after(|f| f.mode = Some("safe".into())),
            Err("seat_trust_changed"),
            "reason: wrote_after_mode_changed"
        );
        assert_eq!(
            recheck("seat_other", &facts(), &approved).map_err(|e| e.1),
            Err("seat_trust_changed"),
            "reason: wrote_for_another_seat"
        );
    }

    #[test]
    fn a_seat_id_is_seat_and_plain_letters() {
        for ok in ["seat_a", "seat_0123abc_x"] {
            assert!(seat_id_ok(ok), "reason: refused_{ok}");
        }
        for bad in [
            "",
            "seat_",
            "a",
            "seat_A",
            "seat_a/b",
            "seat_a b",
            "../seat_a",
            "seat_a\n",
        ] {
            assert!(!seat_id_ok(bad), "reason: took_{bad:?}");
        }
        let long = format!("seat_{}", "a".repeat(65));
        assert!(!seat_id_ok(&long), "reason: took_a_long_id");
        assert!(parse(json!({"type": TRUST, "seat_id": "seat_a", "until": 9})).is_none());
        assert!(parse(json!({"type": TRUST, "seat_id": "seat_a", "hours": 99})).is_none());
        assert!(parse(json!({"type": TRUST, "seat_id": "seat_a", "ticket": "ot_1"})).is_some());
    }
}
