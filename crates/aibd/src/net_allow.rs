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

/// Build the canonical signed params for an "always allow" network grant.
pub fn always_params(persona_uid: &str, addr: SocketAddr, until: u64, request_id: &str) -> Vec<u8> {
    let canon = crate::grants::canon(addr);
    format!("{PREFIX}\n{persona_uid}\n{canon}\n{until}\n{request_id}\n").into_bytes()
}

/// Strictly parse `always_params`: exact 6 lines (trailing newline), valid persona uid,
/// canonical `SocketAddr`, ASCII `until`.
pub fn parse_always_params(raw: &[u8]) -> Option<(String, String, u64)> {
    let text = std::str::from_utf8(raw).ok()?;
    let lines: Vec<&str> = text.split('\n').collect();
    if lines.len() != 6 || !lines[5].is_empty() {
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
    let until: u64 = lines[3].parse().ok()?;
    if lines[4].is_empty() {
        return None;
    }
    Some((uid.to_owned(), canon, until))
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
        let Some((uid, canon, until)) = parse_always_params(&row.params) else {
            continue;
        };
        if uid != persona_uid || canon != want_canon {
            continue;
        }
        if now >= until || until > now.saturating_add(MAX_DAYS_SECS) {
            continue;
        }
        let ask = Ask {
            category: Category::WindowGrant,
            operation: OP_ALWAYS.to_owned(),
            resource: row.resource.clone(),
            params: serde_json::Value::String(String::from_utf8_lossy(&row.params).into_owned()),
            by: row.by_key.clone(),
            top_words: row.resource.clone(),
        };
        let msg = owner_ok::binding(&row.ticket, &ask);
        if !verify_fn(&msg, &row.sig) {
            continue;
        }
        return Some(row);
    }
    None
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

    fn sign_row(ticket: &str, uid: &str, addr: SocketAddr, until: u64, tag: &[u8]) -> NetAllowRow {
        let params = always_params(uid, addr, until, "req_1");
        let resource = format!("{uid} -> {addr}");
        let by = "page:test".to_owned();
        let ask = Ask {
            category: Category::WindowGrant,
            operation: OP_ALWAYS.to_owned(),
            resource: resource.clone(),
            params: serde_json::Value::String(String::from_utf8_lossy(&params).into_owned()),
            by: by.clone(),
            top_words: resource.clone(),
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
        let row = sign_row("tk_1", "un0eba", addr, now + 86_400, b":ok");
        assert!(db.net_allow_add(row).unwrap());

        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &test_verify).is_some());
        assert!(holds(&db, "other1", addr, &own_ips(), now, true, &test_verify).is_none());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, false, &test_verify).is_none());

        // Forged sig is refused
        let forged = sign_row("tk_2", "un0eba", addr, now + 86_400, b":bad");
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

        // > 30 days even if signed
        let long = sign_row("tk_long", "un0eba", addr, now + MAX_DAYS_SECS + 10, b":ok");
        assert!(db.net_allow_add(long).unwrap());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &test_verify).is_none());

        // Own IP is never passed through always-allow
        let own_addr = SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::new(10, 0, 0, 2), 8080));
        let own_row = sign_row("tk_own", "un0eba", own_addr, now + 86_400, b":ok");
        assert!(db.net_allow_add(own_row).unwrap());
        assert!(holds(&db, "un0eba", own_addr, &own_ips(), now, true, &test_verify).is_none());

        // Revoked ticket cannot be re-used even if re-inserted
        let ok = sign_row("tk_rev", "un0eba", addr, now + 86_400, b":ok");
        assert!(db.net_allow_add(ok.clone()).unwrap());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &test_verify).is_some());
        assert!(db.net_allow_revoke("tk_rev", now as i64).unwrap());
        assert!(holds(&db, "un0eba", addr, &own_ips(), now, true, &test_verify).is_none());
        assert!(!db.net_allow_add(ok).unwrap());
    }
}
