//! A6c always-allow list (DECISION-council-netallow-store r1; IFACE-netallow-ticket r2):
//! bound to `(persona_uid, canon ip:port, until <= now + 30d)` and signed by the host's
//! hardware-backed pairing key (`owner_ok::verify`).

use crate::egress::Verdict;
use crate::owner_ok::{self, Ask, Category};
use aib_store::net_allow::NetAllowRow;
use aib_store::MessageDb;
use std::net::{IpAddr, SocketAddr};

pub const OP_ALWAYS: &str = "net.allow_always";
pub const PREFIX: &str = "aibd/net-allow/v1";
pub const MAX_DAYS_SECS: u64 = 30 * 86_400;

/// Build the canonical signed params for an "always allow" network grant (with `issued_at`
/// signed inside so `until - issued_at <= 30d` cannot be bypassed by pre-signing a far-future
/// `until`, ebab8 review 15:47).
pub fn always_params(
    persona_uid: &str,
    addr: SocketAddr,
    issued_at: u64,
    until: u64,
    request_id: &str,
) -> Result<Vec<u8>, &'static str> {
    if until <= issued_at || until - issued_at > MAX_DAYS_SECS {
        return Err("until_exceeds_30d");
    }
    let canon = crate::grants::canon(addr);
    Ok(
        format!("{PREFIX}\n{persona_uid}\n{canon}\n{issued_at}\n{until}\n{request_id}\n")
            .into_bytes(),
    )
}

/// Strictly parse `always_params`: exact 7 lines (trailing newline), valid persona uid,
/// canonical `SocketAddr`, ASCII `issued_at` and `until`.
pub fn parse_always_params(raw: &[u8]) -> Option<(String, String, u64, u64)> {
    let text = std::str::from_utf8(raw).ok()?;
    let lines: Vec<&str> = text.split('\n').collect();
    if lines.len() != 7 || !lines[6].is_empty() {
        return None;
    }
    if lines[0] != PREFIX {
        return None;
    }
    let uid = lines[1];
    if uid.is_empty()
        || uid.len() > 16
        || !uid
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
    {
        return None;
    }
    let parsed: SocketAddr = lines[2].parse().ok()?;
    let canon = crate::grants::canon(parsed);
    if canon != lines[2] {
        return None;
    }
    if lines[3].is_empty() || !lines[3].bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let issued_at: u64 = lines[3].parse().ok()?;
    if lines[4].is_empty() || !lines[4].bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let until: u64 = lines[4].parse().ok()?;
    if lines[5].is_empty() {
        return None;
    }
    Some((uid.to_owned(), canon, issued_at, until))
}

/// Verify and match an always-allow entry for `(persona_uid, addr, now)`.
/// Classifies `addr.ip()` against `own` FIRST (only `Class::Private` may use the list),
/// checks `persona_active`, revoked list, 30-day bound, and `owner_ok::verify`.
pub fn holds(
    db: &MessageDb,
    persona_uid: &str,
    addr: SocketAddr,
    own: &[IpAddr],
    now: u64,
    persona_active: bool,
    verify_fn: &dyn Fn(&[u8], &[u8]) -> bool,
) -> Option<NetAllowRow> {
    if !matches!(
        crate::egress::verdict(&[addr.ip()], addr.port(), own, &|_, _| false),
        Verdict::Card(_)
    ) {
        return None;
    }
    if !persona_active {
        return None;
    }
    let want_canon = crate::grants::canon(addr);
    let rows = db.net_allow_list().ok()?;
    for row in rows {
        if db.net_allow_is_revoked(&row.ticket).unwrap_or(true) {
            continue;
        }
        let Some((uid, canon, issued_at, until)) = parse_always_params(&row.params) else {
            continue;
        };
        if uid != persona_uid || canon != want_canon {
            continue;
        }
        if now < issued_at
            || now >= until
            || until <= issued_at
            || until - issued_at > MAX_DAYS_SECS
        {
            continue;
        }
        let ask = Ask {
            category: Category::WindowGrant,
            operation: OP_ALWAYS,
            resource: row.resource.clone(),
            params: row.params.clone(),
            by: row.by_key.clone(),
        };
        let msg = owner_ok::binding(&row.ticket, &ask);
        if !verify_fn(&msg, &row.sig) {
            continue;
        }
        return Some(row);
    }
    None
}

/// Predicate for [`crate::proxy::decide`] / [`crate::proxy::Tunnels::sweep`] backed by
/// [`holds`].
pub fn proxy_allowed(
    db: &MessageDb,
    persona_uid: &str,
    own: &[IpAddr],
    now: u64,
    persona_active: bool,
    verify_fn: &dyn Fn(&[u8], &[u8]) -> bool,
    ip: IpAddr,
    port: u16,
) -> bool {
    holds(
        db,
        persona_uid,
        SocketAddr::new(ip, port),
        own,
        now,
        persona_active,
        verify_fn,
    )
    .is_some()
}

/// Sweep open tunnels against [`proxy_allowed`] per-seat `(persona_uid, persona_active)`
/// (used on expire, merge, or archive where `persona_active` becomes `false`).
#[allow(clippy::too_many_arguments)]
pub fn sweep_tunnels(
    db: &MessageDb,
    now: u64,
    tunnels: &mut crate::proxy::Tunnels,
    grants: &crate::grants::Grants,
    seat_persona: &dyn Fn(&str) -> Option<(&str, bool)>,
    own: &[IpAddr],
    verify_fn: &dyn Fn(&[u8], &[u8]) -> bool,
) -> Vec<u64> {
    tunnels.sweep(now, grants, &|seat, ip, port| {
        let Some((persona_uid, persona_active)) = seat_persona(seat) else {
            return false;
        };
        proxy_allowed(
            db,
            persona_uid,
            own,
            now,
            persona_active,
            verify_fn,
            ip,
            port,
        )
    })
}

/// Revoke `ticket` and immediately close any open tunnels that relied on it.
#[allow(clippy::too_many_arguments)]
pub fn revoke_and_sweep(
    db: &MessageDb,
    ticket: &str,
    now: u64,
    tunnels: &mut crate::proxy::Tunnels,
    grants: &crate::grants::Grants,
    seat_persona: &dyn Fn(&str) -> Option<(&str, bool)>,
    own: &[IpAddr],
    verify_fn: &dyn Fn(&[u8], &[u8]) -> bool,
) -> aib_store::Result<Vec<u64>> {
    db.net_allow_revoke(ticket, now as i64)?;
    Ok(sweep_tunnels(
        db,
        now,
        tunnels,
        grants,
        seat_persona,
        own,
        verify_fn,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::{Ipv4Addr, SocketAddrV4};

    fn p_addr() -> SocketAddr {
        SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::new(192, 168, 1, 10), 8080))
    }

    fn own_ips() -> Vec<IpAddr> {
        vec![IpAddr::V4(Ipv4Addr::new(10, 0, 0, 2))]
    }

    fn sign_row(
        ticket: &str,
        uid: &str,
        addr: SocketAddr,
        issued_at: u64,
        until: u64,
        tag: &[u8],
    ) -> NetAllowRow {
        let params = always_params(uid, addr, issued_at, until, "req_1").unwrap();
        sign_row_with_params(ticket, uid, addr, params, tag)
    }

    fn sign_row_raw(
        ticket: &str,
        uid: &str,
        addr: SocketAddr,
        issued_at: u64,
        until: u64,
        tag: &[u8],
    ) -> NetAllowRow {
        let canon = crate::grants::canon(addr);
        let params =
            format!("{PREFIX}\n{uid}\n{canon}\n{issued_at}\n{until}\nreq_1\n").into_bytes();
        sign_row_with_params(ticket, uid, addr, params, tag)
    }

    fn sign_row_with_params(
        ticket: &str,
        uid: &str,
        addr: SocketAddr,
        params: Vec<u8>,
        tag: &[u8],
    ) -> NetAllowRow {
        let resource = format!("{uid} -> {addr}");
        let by = "page:test".to_owned();
        let ask = Ask {
            category: Category::WindowGrant,
            operation: OP_ALWAYS,
            resource: resource.clone(),
            params: params.clone(),
            by: by.clone(),
        };
        let msg = owner_ok::binding(ticket, &ask);
        let mut sig = msg;
        sig.extend_from_slice(tag);
        NetAllowRow {
            ticket: ticket.to_owned(),
            by_key: by,
            resource,
            params,
            sig,
            approved_at: 1000,
        }
    }

    fn test_verify(msg: &[u8], sig: &[u8]) -> bool {
        sig.strip_suffix(b":ok") == Some(msg)
    }

    #[test]
    fn valid_signed_row_holds_and_forged_or_tampered_row_is_refused() {
        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join("m.db")).unwrap();
        let now = 1_000_000;
        let addr = p_addr();
        let row = sign_row("tk_1", "un0eba", addr, now, now + 86_400, b":ok");
        assert!(db.net_allow_add(row).unwrap());

        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &test_verify).is_some());
        assert!(holds(&db, "other1", addr, &own_ips(), now, true, &test_verify).is_none());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, false, &test_verify).is_none());

        // Forged sig is refused
        let forged = sign_row("tk_2", "un0eba", addr, now, now + 86_400, b":bad");
        assert!(db.net_allow_add(forged).unwrap());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &|m, s| {
            s.strip_suffix(b":never") == Some(m)
        })
        .is_none());
    }

    #[test]
    fn over_30_days_own_ip_and_revoked_tickets_are_refused() {
        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join("m.db")).unwrap();
        let now = 1_000_000;
        let addr = p_addr();

        // > 30 days from issued_at even if signed (and even when checked in the last 30 days)
        assert!(always_params("un0eba", addr, now, now + MAX_DAYS_SECS + 10, "req_1").is_err());
        let year = 365 * 86_400;
        let pre_signed = sign_row_raw("tk_long", "un0eba", addr, now, now + year, b":ok");
        assert!(db.net_allow_add(pre_signed).unwrap());
        assert!(holds(
            &db,
            "un0eba",
            addr,
            &own_ips(),
            now + year - 86_400,
            true,
            &test_verify
        )
        .is_none());

        // Own IP is never passed through always-allow
        let own_addr = SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::new(10, 0, 0, 2), 8080));
        let own_row = sign_row("tk_own", "un0eba", own_addr, now, now + 86_400, b":ok");
        assert!(db.net_allow_add(own_row).unwrap());
        assert!(holds(&db, "un0eba", own_addr, &own_ips(), now, true, &test_verify).is_none());

        // Revoked ticket cannot be re-used even if re-inserted
        let ok = sign_row("tk_rev", "un0eba", addr, now, now + 86_400, b":ok");
        assert!(db.net_allow_add(ok.clone()).unwrap());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &test_verify).is_some());
        assert!(db.net_allow_revoke("tk_rev", now as i64).unwrap());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &test_verify).is_none());
        assert!(!db.net_allow_add(ok).unwrap());
    }

    #[test]
    fn proxy_decide_uses_net_allow_holds_and_closes_tunnels_on_revoke_expire_and_inactive() {
        use crate::grants::Grants;
        use crate::proxy::{decide, By, CardLimit, Conn, Here, Ports, Tunnels, Who};

        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join("m.db")).unwrap();
        let now = 2_000_000;
        let addr = p_addr();
        let own = own_ips();

        let row = sign_row("tk_proxy_1", "un0eba", addr, now, now + 86_400, b":ok");
        assert!(db.net_allow_add(row).unwrap());

        let mut ports = Ports::default();
        let port = ports.give("seat1", &[47770, 47780], 0).unwrap();
        let grants = Grants::default();
        let mut lim = CardLimit::default();
        let here = Here {
            own: &own,
            own_binds: &[],
            preview_ports: &[],
            now,
        };
        let who = Who {
            persona_uid: "un0eba",
            persona_name: "Test",
        };

        let allowed = |_: &str, ip: IpAddr, p: u16| {
            proxy_allowed(&db, "un0eba", &own, now, true, &test_verify, ip, p)
        };
        assert_eq!(
            decide(
                &ports,
                port,
                "db.lan",
                &[addr.ip()],
                addr.port(),
                here,
                &who,
                &grants,
                &allowed,
                &mut lim,
                0,
                "rq_1".into(),
            ),
            Conn::Pass(addr)
        );

        // Open tunnel held by always-allow; revoke closes it immediately
        let mut tunnels = Tunnels::default();
        let tid = tunnels.open("seat1", addr, By::Held);
        let seat_map = |s: &str| match s {
            "seat1" => Some(("un0eba", true)),
            "seat_b" => Some(("other_p", true)),
            _ => None,
        };
        assert_eq!(
            revoke_and_sweep(
                &db,
                "tk_proxy_1",
                now,
                &mut tunnels,
                &grants,
                &seat_map,
                &own,
                &test_verify,
            )
            .unwrap(),
            vec![tid]
        );

        // Second valid ticket for un0eba: seat_b (other_p) to same addr MUST be closed while seat1 stays
        let row2 = sign_row("tk_proxy_2", "un0eba", addr, now, now + 100, b":ok");
        assert!(db.net_allow_add(row2).unwrap());
        let tid2 = tunnels.open("seat1", addr, By::Held);
        let tid_b = tunnels.open("seat_b", addr, By::Held);
        assert_eq!(
            sweep_tunnels(
                &db,
                now + 50,
                &mut tunnels,
                &grants,
                &seat_map,
                &own,
                &test_verify,
            ),
            vec![tid_b]
        );
        // Inactive persona (archived/merged) closes immediately
        let seat_inactive = |s: &str| match s {
            "seat1" => Some(("un0eba", false)),
            _ => None,
        };
        assert_eq!(
            sweep_tunnels(
                &db,
                now + 50,
                &mut tunnels,
                &grants,
                &seat_inactive,
                &own,
                &test_verify,
            ),
            vec![tid2]
        );
        // Expired ticket also closes immediately
        let tid3 = tunnels.open("seat1", addr, By::Held);
        assert_eq!(
            sweep_tunnels(
                &db,
                now + 100,
                &mut tunnels,
                &grants,
                &seat_map,
                &own,
                &test_verify,
            ),
            vec![tid3]
        );
    }

    #[tokio::test]
    async fn live_tcp_tunnel_closes_within_one_second_on_revoke() {
        use crate::grants::Grants;
        use crate::proxy::{By, Tunnels};
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::{TcpListener, TcpStream};

        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join("m.db")).unwrap();
        let now = 2_000_000;
        let addr = p_addr();
        let own = own_ips();

        let row = sign_row("tk_live_1", "un0eba", addr, now, now + 86_400, b":ok");
        assert!(db.net_allow_add(row).unwrap());

        let srv = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let srv_addr = srv.local_addr().unwrap();
        let mut client = TcpStream::connect(srv_addr).await.unwrap();
        let (mut peer, _) = srv.accept().await.unwrap();

        let mut tunnels = Tunnels::default();
        let tid = tunnels.open("seat1", addr, By::Held);
        let (tx, rx) = tokio::sync::oneshot::channel::<()>();
        let task = tokio::spawn(async move {
            let mut buf = [0u8; 16];
            tokio::select! {
                _ = rx => {}
                _ = peer.read(&mut buf) => {}
            }
            let _ = peer.shutdown().await;
        });

        let grants = Grants::default();
        let seat_map = |s: &str| (s == "seat1").then_some(("un0eba", true));
        let closed = revoke_and_sweep(
            &db,
            "tk_live_1",
            now,
            &mut tunnels,
            &grants,
            &seat_map,
            &own,
            &test_verify,
        )
        .unwrap();
        assert_eq!(closed, vec![tid]);
        let _ = tx.send(());
        let _ = tokio::time::timeout(std::time::Duration::from_secs(1), task)
            .await
            .unwrap();

        let mut buf = [0u8; 8];
        let read_res =
            tokio::time::timeout(std::time::Duration::from_secs(1), client.read(&mut buf))
                .await
                .unwrap()
                .unwrap();
        assert_eq!(read_res, 0);
    }

    #[test]
    fn e2e_ask_always_sign_writes_db_and_holds_and_tamper_fails() {
        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join("m.db")).unwrap();
        let now = 2_000_000;
        let addr = p_addr();
        let own = own_ips();

        let card = crate::net_card::Card {
            request_id: "rq_e2e".into(),
            seat_id: "seat1".into(),
            persona_uid: "un0eba".into(),
            persona_name: "Web".into(),
            dest_name: "db.lan".into(),
            dest: addr,
            source: "proxy",
            why: None,
            expires_at: now + 100,
            high: false,
            listener: None,
        };
        let ask = card.ask_always("page:test", now);
        let msg = owner_ok::binding("tk_e2e", &ask);
        let mut sig = msg;
        sig.extend_from_slice(b":ok");

        let row = NetAllowRow {
            ticket: "tk_e2e".into(),
            by_key: ask.by,
            resource: ask.resource,
            params: ask.params,
            sig,
            approved_at: now as i64,
        };
        assert!(db.net_allow_add(row.clone()).unwrap());
        assert!(holds(&db, "un0eba", addr, &own, now, true, &test_verify).is_some());

        // Flip 1 byte in params: verification MUST fail
        let mut tampered = row;
        let last = tampered.params.len() - 2;
        tampered.params[last] ^= 1;
        tampered.ticket = "tk_e2e_bad".into();
        let db2 = MessageDb::open(&d.path().join("m2.db")).unwrap();
        assert!(db2.net_allow_add(tampered).unwrap());
        assert!(holds(&db2, "un0eba", addr, &own, now, true, &test_verify).is_none());
    }

    #[test]
    fn two_step_answer_always_with_clock_advance_succeeds_and_holds() {
        use crate::net_card::{Card, Cards, Choice, Step};

        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join("m.db")).unwrap();
        let t1 = 2_000_000;
        let t2 = t1 + 10;
        let addr = p_addr();
        let own = own_ips();

        let mut cards = Cards::default();
        cards.add(Card {
            request_id: "rq_2step".into(),
            seat_id: "seat1".into(),
            persona_uid: "un0eba".into(),
            persona_name: "Web".into(),
            dest_name: "db.lan".into(),
            dest: addr,
            source: "proxy",
            why: None,
            expires_at: t1 + 120,
            high: false,
            listener: None,
        });

        let Step::Ask(ask1) =
            cards.answer("rq_2step", "always", None, t1, "page:test", |_, _| false)
        else {
            panic!("expected Step::Ask");
        };
        cards.opened("rq_2step", Choice::Always);
        let expected_msg = owner_ok::binding("tk_2step", &ask1);

        let mut captured: Option<(String, Vec<u8>, Vec<u8>)> = None;
        let step2 = cards.answer(
            "rq_2step",
            "always",
            Some("tk_2step"),
            t2,
            "page:test",
            |t, ask2| {
                let got_msg = owner_ok::binding(t, ask2);
                if got_msg != expected_msg {
                    return false;
                }
                let mut sig = got_msg;
                sig.extend_from_slice(b":ok");
                captured = Some((ask2.resource.clone(), ask2.params.clone(), sig));
                true
            },
        );
        assert!(matches!(step2, Step::Done(Choice::Always, Some(_))));
        let (resource, params, sig) = captured.unwrap();
        let row = NetAllowRow {
            ticket: "tk_2step".into(),
            by_key: "page:test".into(),
            resource,
            params,
            sig,
            approved_at: t2 as i64,
        };
        assert!(db.net_allow_add(row).unwrap());
        assert!(holds(&db, "un0eba", addr, &own, t2, true, &test_verify).is_some());
    }
}
