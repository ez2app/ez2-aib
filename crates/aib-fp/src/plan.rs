//! The steps of one install and the plan that says, for each, whether it is done, to do,
//! waiting or stopped — and with which values a step that is to do will be done.
//!
//! The plan has two parts (PREREAD M1):
//! - **deciding**: the request, and for every step its state and the values it would
//!   write. Only this is hashed ([`Plan::decide_hash`]); `--apply` re-reads under the
//!   server's lock and goes on only when the hash is the one that was approved, so what
//!   was approved is what is done;
//! - **observed**: what was seen on the way (event counts, cache ages, question counts,
//!   times). Shown, never hashed: it changes on its own between the plan and the apply.

use crate::request::{Kind, Request};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

/// The steps, in the order they are done (SPEC r1 table; DESIGN r1 §3 / §7).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Step {
    /// What must hold before anything is written.
    Before,
    /// The tenant, both names, the site.
    Tenant,
    /// The server's `/e2/` route file.
    Route,
    /// The Cloudflare cache rule for `/e2/*`.
    CfRule,
    /// Tracking on.
    Tracking,
    /// The site plugin and its settings.
    Plugin,
    /// The page caches cleared (two or three layers).
    Caches,
    /// Real visitors' events arrive.
    Events,
    /// LINE's webhook is not blocked.
    LineHook,
    /// The answer page's subdomain: DNS, route, certificate.
    SubHost,
    /// The answer page in the engine: domain, settings, place.
    SubEngine,
    /// The AI key, the AI switched on, questions on.
    Ai,
    /// "Today" and the eight features (on for every shop already; only checked).
    Today,
    /// A shop's purchase checks (its own slice; listed only).
    Purchases,
    /// Health alerts for this shop (none exist per shop yet; listed only).
    Alerts,
}

/// Every step, in order.
pub const STEPS: [Step; 15] = [
    Step::Before,
    Step::Tenant,
    Step::Route,
    Step::CfRule,
    Step::Tracking,
    Step::Plugin,
    Step::Caches,
    Step::Events,
    Step::LineHook,
    Step::SubHost,
    Step::SubEngine,
    Step::Ai,
    Step::Today,
    Step::Purchases,
    Step::Alerts,
];

impl Step {
    /// The number the hand-written steps had (SOP / SPEC table), as shown.
    pub fn number(self) -> &'static str {
        match self {
            Step::Before => "0",
            Step::Tenant => "1",
            Step::Route => "2",
            Step::CfRule => "2b",
            Step::Tracking => "3",
            Step::Plugin => "4",
            Step::Caches => "4b",
            Step::Events => "5",
            Step::LineHook => "6",
            Step::SubHost => "7",
            Step::SubEngine => "8",
            Step::Ai => "8b",
            Step::Today => "9",
            Step::Purchases => "10",
            Step::Alerts => "11",
        }
    }

    /// Whether `--apply` may write in this step. The others are read only: they are
    /// shown, and a person or another slice does what they ask for.
    pub fn writes(self) -> bool {
        !matches!(
            self,
            Step::Before
                | Step::Events
                | Step::LineHook
                | Step::Today
                | Step::Purchases
                | Step::Alerts
        )
    }

    /// Whether this step is part of an install of this kind.
    pub fn applies(self, kind: Kind) -> bool {
        self != Step::Purchases || kind == Kind::Shop
    }
}

/// Where one step stands.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    /// Already the way it should be: skipped.
    Done,
    /// To be done by `--apply`, with these values.
    Todo,
    /// Nothing wrong, but not there yet (no visitors yet, cache cooling down): ⚠️.
    Wait(&'static str),
    /// Read only and needs a person, or differs from the standard without being wrong:
    /// ⚠️, never written.
    Warn(&'static str),
    /// Cannot go on (a tenant or domain someone else has, an unreadable answer): ❌.
    /// Nothing after it is done.
    Stop(String),
}

impl State {
    pub fn mark(&self) -> &'static str {
        match self {
            State::Done => "✅",
            State::Todo => "❌",
            State::Wait(_) | State::Warn(_) => "⚠️",
            State::Stop(_) => "⛔",
        }
    }

    fn word(&self) -> String {
        match self {
            State::Done => "done".into(),
            State::Todo => "todo".into(),
            State::Wait(w) => format!("wait:{w}"),
            State::Warn(w) => format!("warn:{w}"),
            State::Stop(w) => format!("stop:{w}"),
        }
    }
}

/// One step of the plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub step: Step,
    pub state: State,
    /// What the step writes (deciding: hashed). Never a secret: a key is named by
    /// "present" / "absent" or the first 8 of its fingerprint.
    pub values: BTreeMap<String, String>,
    /// What was seen (observed: not hashed).
    pub seen: BTreeMap<String, String>,
}

impl Row {
    pub fn new(step: Step, state: State) -> Row {
        Row {
            step,
            state,
            values: BTreeMap::new(),
            seen: BTreeMap::new(),
        }
    }
}

/// The whole plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub request: Request,
    pub rows: Vec<Row>,
}

/// Characters a value may not carry into the canonical form (it is line and tab
/// separated).
fn clean(s: &str) -> bool {
    !s.contains(['\n', '\r', '\t'])
}

impl Plan {
    /// Builds the plan from the request and one row per step, in [`STEPS`] order; a step
    /// that does not apply to this kind is left out. Rows out of order, twice, missing,
    /// or carrying a line break in a value are refused.
    pub fn new(request: Request, rows: Vec<Row>) -> Result<Plan, &'static str> {
        request.check()?;
        let want: Vec<Step> = STEPS
            .iter()
            .copied()
            .filter(|s| s.applies(request.kind))
            .collect();
        let have: Vec<Step> = rows.iter().map(|r| r.step).collect();
        if have != want {
            return Err("steps_out_of_order");
        }
        let all_clean = rows.iter().all(|r| {
            r.values.iter().all(|(k, v)| clean(k) && clean(v))
                && r.seen.iter().all(|(k, v)| clean(k) && clean(v))
                && match &r.state {
                    State::Stop(why) => clean(why),
                    _ => true,
                }
        });
        if !all_clean {
            return Err("line_break_in_value");
        }
        // each shop gives its own brand and subdomain for the answer page (PREREAD L3)
        let sub_todo = rows
            .iter()
            .any(|r| matches!(r.step, Step::SubHost | Step::SubEngine) && r.state == State::Todo);
        if sub_todo && request.brand.is_none() {
            return Err("brand_required");
        }
        if sub_todo && request.sub.is_none() {
            return Err("sub_required");
        }
        Ok(Plan { request, rows })
    }

    /// The deciding part, the only input to [`Plan::decide_hash`]:
    /// `aib-fp/plan/v1`, the request one field a line, then one line per step:
    /// `<number>\t<what it decides>\t<values as one line of JSON, keys sorted>` (JSON so a
    /// value holding `;` or `=` cannot pass for two; F1 review L1). Fixed order and
    /// separators; changing them is a new version.
    pub fn canonical(&self) -> String {
        let r = &self.request;
        let mut s = format!(
            "aib-fp/plan/v1\nslug={}\ndomain={}\nkind={}\nlike={}\nbrand={}\nsub={}\n",
            r.slug,
            r.domain,
            r.kind.word(),
            r.like.as_deref().unwrap_or("-"),
            r.brand.as_deref().unwrap_or("-"),
            r.sub.as_deref().unwrap_or("-"),
        );
        for row in &self.rows {
            let vals = serde_json::to_string(&row.values).unwrap_or_default();
            s.push_str(&format!(
                "{}\t{}\t{}\n",
                row.step.number(),
                decided(row),
                vals
            ));
        }
        s
    }

    /// SHA-256 of [`Plan::canonical`], hex. What an approval is bound to; observed
    /// values are not in it.
    pub fn decide_hash(&self) -> String {
        let d = Sha256::digest(self.canonical().as_bytes());
        d.iter().map(|b| format!("{b:02x}")).collect()
    }

    /// The first stopped step: nothing from it on is done.
    pub fn stopped_at(&self) -> Option<Step> {
        self.rows
            .iter()
            .find(|r| matches!(r.state, State::Stop(_)))
            .map(|r| r.step)
    }

    /// The steps `--apply` would write, in order: those to do that write, up to the
    /// first stop.
    pub fn to_write(&self) -> Vec<Step> {
        self.rows
            .iter()
            .take_while(|r| !matches!(r.state, State::Stop(_)))
            .filter(|r| r.state == State::Todo && r.step.writes())
            .map(|r| r.step)
            .collect()
    }

    /// Every applying step done, except the ones allowed to wait or warn (`allowed`).
    pub fn all_done_but(&self, allowed: &[Step]) -> bool {
        self.rows.iter().all(|r| {
            r.state == State::Done
                || (allowed.contains(&r.step) && matches!(r.state, State::Wait(_) | State::Warn(_)))
        })
    }

    /// The plan as JSON for the sign sheet and `--json` (`decide_hash` included).
    pub fn json(&self) -> serde_json::Value {
        let rows: Vec<serde_json::Value> = self
            .rows
            .iter()
            .map(|r| {
                serde_json::json!({
                    "step": r.step.number(),
                    "state": r.state.word(),
                    "values": r.values,
                    "seen": r.seen,
                })
            })
            .collect();
        serde_json::json!({
            "v": 1,
            "slug": self.request.slug,
            "domain": self.request.domain,
            "kind": self.request.kind.word(),
            "like": self.request.like,
            "brand": self.request.brand,
            "sub": self.request.sub,
            "decide_hash": self.decide_hash(),
            "steps": rows,
        })
    }
}

/// What a row decides, as hashed (F1 review M2): whether a step that writes will write
/// (`todo` / `skip`), and any stop with its reason. Whether a read-only step is done or
/// waiting, and why a step waits or warns, is observed: questions appearing or the first
/// visitor arriving between the plan and the Touch ID must not stop the apply.
fn decided(row: &Row) -> String {
    match &row.state {
        State::Stop(why) => format!("stop:{why}"),
        State::Todo if row.step.writes() => "todo".into(),
        _ if row.step.writes() => "skip".into(),
        _ => "-".into(),
    }
}

/// `--apply` on the server: the plan re-read under the lock against the approved hash.
/// Different ⇒ stop, nothing written (something changed what would be done).
pub fn same_plan(approved_hash: &str, now: &Plan) -> Result<(), &'static str> {
    if approved_hash.len() == 64 && approved_hash == now.decide_hash() {
        Ok(())
    } else {
        Err("plan_changed")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(kind: Kind) -> Request {
        Request {
            slug: "wuxuan".into(),
            domain: "wuxuanbags.com".into(),
            kind,
            like: Some("tahokkaido".into()),
            brand: Some("吳軒手工皮包".into()),
            sub: Some("aigc".into()),
        }
    }

    fn rows(kind: Kind) -> Vec<Row> {
        STEPS
            .iter()
            .copied()
            .filter(|s| s.applies(kind))
            .map(|s| Row::new(s, State::Done))
            .collect()
    }

    fn plan() -> Plan {
        let mut r = rows(Kind::Content);
        r[3].state = State::Todo;
        r[3].values
            .insert("browser_ttl".into(), "respect_origin".into());
        r[7].state = State::Wait("waiting_for_visitors");
        r[7].seen.insert("events_5m".into(), "0".into());
        Plan::new(req(Kind::Content), r).unwrap()
    }

    /// PREREAD M1: what is only seen may change between plan and apply; what is decided
    /// may not.
    #[test]
    fn an_observed_value_changing_keeps_the_plan_and_a_decided_one_stops_it() {
        let p = plan();
        let h = p.decide_hash();
        assert_eq!(h.len(), 64);
        let mut seen_moved = p.clone();
        seen_moved.rows[7]
            .seen
            .insert("events_5m".into(), "12".into());
        seen_moved.rows[3].seen.insert("cf_age".into(), "40".into());
        assert_eq!(
            same_plan(&h, &seen_moved),
            Ok(()),
            "reason: observed_value_in_hash"
        );
        let mut value_moved = p.clone();
        value_moved.rows[3]
            .values
            .insert("browser_ttl".into(), "override".into());
        assert_eq!(
            same_plan(&h, &value_moved),
            Err("plan_changed"),
            "reason: decided_value_not_in_hash"
        );
        let mut state_moved = p.clone();
        state_moved.rows[1].state = State::Todo;
        assert_eq!(same_plan(&h, &state_moved), Err("plan_changed"));
        let mut req_moved = p.clone();
        req_moved.request.brand = Some("北海道".into());
        assert_eq!(
            same_plan(&h, &req_moved),
            Err("plan_changed"),
            "reason: request_not_in_hash"
        );
        assert_eq!(same_plan("", &p), Err("plan_changed"));
    }

    #[test]
    fn the_steps_are_all_there_once_and_in_order() {
        assert_eq!(
            Plan::new(req(Kind::Content), rows(Kind::Shop)).err(),
            Some("steps_out_of_order"),
            "reason: purchases_in_a_content_install"
        );
        assert!(Plan::new(req(Kind::Shop), rows(Kind::Shop)).is_ok());
        let mut r = rows(Kind::Content);
        r.swap(1, 2);
        assert_eq!(
            Plan::new(req(Kind::Content), r).err(),
            Some("steps_out_of_order")
        );
        let mut r = rows(Kind::Content);
        r.pop();
        assert_eq!(
            Plan::new(req(Kind::Content), r).err(),
            Some("steps_out_of_order")
        );
        let mut r = rows(Kind::Content);
        r[1].state = State::Stop("x\n6\t-\t{}".into());
        assert_eq!(
            Plan::new(req(Kind::Content), r).err(),
            Some("line_break_in_value"),
            "reason: stop_reason_can_forge_a_line"
        );
        let mut r = rows(Kind::Content);
        r[1].values.insert("x".into(), "a\nb".into());
        assert_eq!(
            Plan::new(req(Kind::Content), r).err(),
            Some("line_break_in_value"),
            "reason: value_can_forge_a_line"
        );
        let mut bad = req(Kind::Content);
        bad.slug = "X".into();
        assert_eq!(Plan::new(bad, rows(Kind::Content)).err(), Some("bad_slug"));
    }

    /// F1 review M2: the questions appearing, the first visitor, LINE answering — between
    /// the plan and the Touch ID — keep the plan; a step that writes turning from done to
    /// to-do does not.
    #[test]
    fn what_only_reads_may_change_state_and_what_writes_may_not() {
        let p = plan();
        let h = p.decide_hash();
        let mut q = p.clone();
        q.rows[11].state = State::Wait("questions_being_made");
        let hq = q.decide_hash();
        q.rows[11].state = State::Done;
        assert_eq!(
            same_plan(&hq, &q),
            Ok(()),
            "reason: questions_appearing_stop_apply"
        );
        let mut v = p.clone();
        v.rows[7].state = State::Done;
        assert_eq!(
            same_plan(&h, &v),
            Ok(()),
            "reason: first_visitor_stops_apply"
        );
        v.rows[8].state = State::Warn("press_verify_in_line_developers");
        assert_eq!(same_plan(&h, &v), Ok(()));
        let mut w = p.clone();
        w.rows[5].state = State::Todo;
        assert_eq!(
            same_plan(&h, &w),
            Err("plan_changed"),
            "reason: new_write_passed"
        );
        let mut x = p.clone();
        x.rows[8].state = State::Stop("line_webhook_blocked".into());
        assert_eq!(
            same_plan(&h, &x),
            Err("plan_changed"),
            "reason: new_stop_passed"
        );
    }

    /// F1 review L1: a value holding the separators is one value.
    #[test]
    fn a_value_with_separators_cannot_pass_for_two() {
        let mut a = plan();
        a.rows[3].values.clear();
        a.rows[3].values.insert("a".into(), "1;b=2".into());
        let mut b = plan();
        b.rows[3].values.clear();
        b.rows[3].values.insert("a".into(), "1".into());
        b.rows[3].values.insert("b".into(), "2".into());
        assert_ne!(a.decide_hash(), b.decide_hash(), "reason: values_collide");
    }

    /// F1 review L3: the answer page's brand and subdomain are each shop's own.
    #[test]
    fn an_answer_page_to_make_needs_the_shops_brand_and_subdomain() {
        let mut r = rows(Kind::Content);
        r[9].state = State::Todo;
        let mut no_brand = req(Kind::Content);
        no_brand.brand = None;
        assert_eq!(
            Plan::new(no_brand, r.clone()).err(),
            Some("brand_required"),
            "reason: reference_brand_used"
        );
        let mut no_sub = req(Kind::Content);
        no_sub.sub = None;
        assert_eq!(Plan::new(no_sub, r).err(), Some("sub_required"));
        let mut done = req(Kind::Content);
        done.brand = None;
        assert!(Plan::new(done, rows(Kind::Content)).is_ok());
    }

    #[test]
    fn apply_writes_only_steps_to_do_up_to_the_first_stop() {
        let mut p = plan();
        assert_eq!(p.to_write(), vec![Step::CfRule]);
        p.rows[1].state = State::Stop("domain_in_other_tenant:t_ab".into());
        assert_eq!(p.stopped_at(), Some(Step::Tenant));
        assert_eq!(
            p.to_write(),
            Vec::<Step>::new(),
            "reason: wrote_past_a_stop"
        );
        let mut p = plan();
        p.rows[8].state = State::Todo; // LINE webhook is read only
        assert_eq!(
            p.to_write(),
            vec![Step::CfRule],
            "reason: read_only_step_written"
        );
    }

    #[test]
    fn done_means_every_step_but_the_ones_allowed_to_wait() {
        let p = plan();
        assert!(
            !p.all_done_but(&[Step::Events]),
            "reason: todo_counted_done"
        );
        let mut p = plan();
        p.rows[3].state = State::Done;
        assert!(p.all_done_but(&[Step::Events]));
        assert!(!p.all_done_but(&[]), "reason: wait_counted_done");
        assert_eq!(State::Todo.mark(), "❌");
        assert_eq!(State::Wait("x").mark(), "⚠️");
    }

    #[test]
    fn the_plan_json_carries_its_hash_and_both_parts() {
        let p = plan();
        let j = p.json();
        assert_eq!(j["decide_hash"], p.decide_hash());
        assert_eq!(j["steps"][3]["step"], "2b");
        assert_eq!(j["steps"][3]["state"], "todo");
        assert_eq!(j["steps"][7]["seen"]["events_5m"], "0");
        assert_eq!(j["steps"][7]["state"], "wait:waiting_for_visitors");
        assert_eq!(
            j["brand"], "吳軒手工皮包",
            "reason: approval_window_without_brand"
        );
        assert_eq!(j["like"], "tahokkaido");
        assert_eq!(j["sub"], "aigc");
    }
}
