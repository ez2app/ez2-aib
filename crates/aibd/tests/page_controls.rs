#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! Andy 11-19 00:2x: the browser's own grey button showed up three times in one night
//! (再看 N 筆, 暫不處理 N 則, the old add-seat dialog). Every control the workspace page
//! draws must carry a class, so it takes the workspace look instead of the browser default.
//! This reads `apps/web/dist/ws.js` and fails on any `<button`, `<select`, `<textarea`,
//! `<details`, `<summary` or `<input` (other than type="hidden") written without `class="`.

use std::path::Path;

const TAGS: [&str; 6] = [
    "button", "select", "textarea", "details", "summary", "input",
];

/// The opening tag starting at `start` (at its `<`), up to the `>` that is not inside a
/// `${ … }` template expression.
fn opening_tag(js: &str, start: usize) -> &str {
    let b = js.as_bytes();
    let mut depth = 0usize;
    let mut i = start + 1;
    while i < b.len() {
        match b[i] {
            b'$' if b.get(i + 1) == Some(&b'{') => {
                depth += 1;
                i += 1;
            }
            b'}' if depth > 0 => depth -= 1,
            b'>' if depth == 0 => return &js[start..=i],
            _ => {}
        }
        i += 1;
    }
    &js[start..]
}

fn bare_controls(js: &str) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    for tag in TAGS {
        let open = format!("<{tag}");
        let mut from = 0;
        while let Some(rel) = js[from..].find(&open) {
            let at = from + rel;
            from = at + open.len();
            // `<input` must not match `<inputs`, `<button` not `<buttons` …
            let next = js.as_bytes().get(at + open.len()).copied().unwrap_or(b' ');
            if next.is_ascii_alphanumeric() || next == b'-' || next == b'_' {
                continue;
            }
            let t = opening_tag(js, at);
            if t.contains("type=\"hidden\"") || t.contains("class=\"") {
                continue;
            }
            let line = js[..at].matches('\n').count() + 1;
            out.push((line, t.chars().take(100).collect()));
        }
    }
    out.sort();
    out
}

#[test]
fn every_control_on_the_workspace_page_has_a_class() {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/web/dist/ws.js");
    let js = std::fs::read_to_string(p).unwrap();
    let bad = bare_controls(&js);
    assert!(
        bad.is_empty(),
        "controls without a class (they would show the browser's default look):\n{}",
        bad.iter()
            .map(|(l, t)| format!("  ws.js:{l} {t}"))
            .collect::<Vec<_>>()
            .join("\n")
    );
}

#[test]
fn the_check_itself_catches_a_bare_button_and_skips_classed_ones() {
    let js = "a\n<button type=\"button\" data-x=\"${a > b ? 1 : 2}\">x</button>\n\
              <button class=\"k\">y</button><input type=\"hidden\" name=\"n\">\
              <inputs><summary>s</summary>";
    let bad = bare_controls(js);
    assert_eq!(bad.len(), 2, "{bad:?}");
    assert_eq!(bad[0].0, 2);
    assert!(bad[0].1.contains("${a > b ? 1 : 2}"), "{bad:?}");
    assert!(bad[1].1.starts_with("<summary"), "{bad:?}");
}

/// 519d0 11-19 12:09 (Andy pressed his finger three times today): in a preview, a pairing carried
/// over a swap is sent back to the parent's 打開 (a fresh pass, no code, no finger), not told to
/// pair again.
#[test]
fn in_a_preview_a_carried_pairing_is_sent_back_to_open() {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/web/dist/ws.js");
    let js = std::fs::read_to_string(p).unwrap();
    for code in [
        "carried_pair_read_and_safe_only",
        "carried_pair_safe_seats_only",
    ] {
        let at = format!("if (e.code === '{code}') return ");
        let line = js.lines().find(|l| l.contains(&at)).unwrap();
        assert!(
            line.contains("return inPreview() ? PV_CARRIED : "),
            "reason: preview_carried_told_to_repair {line}"
        );
    }
    let says = js
        .lines()
        .find(|l| l.contains("const PV_CARRIED = "))
        .unwrap();
    assert!(
        says.contains("「打開」") && !says.contains("重新配對"),
        "reason: pv_carried_not_open {says}"
    );
}
