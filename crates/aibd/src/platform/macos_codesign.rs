//! T97-3m (043dd, IFACE-T97-3m-043dd-1117.md r2): the only place aibd runs `/usr/bin/codesign`.
//! SPEC-s107-product r4 §11 platform-tool exception: a fixed absolute path, fixed argument
//! arrays built here, never a shell, never PATH. `crate::install::update` reaches it only
//! through the [`crate::install::update::Codesign`] trait, so tests never sign anything.

use std::path::Path;
use std::process::{Command, Stdio};

use crate::install::update::{parse_requirement, Codesign};

/// Fixed path; PATH is never consulted (§11).
pub const CODESIGN: &str = "/usr/bin/codesign";

pub struct Real;

fn run(args: &[&str]) -> Result<(i32, String, String), String> {
    let o = Command::new(CODESIGN)
        .args(args)
        .stdin(Stdio::null())
        .env_clear()
        .output()
        .map_err(|e| format!("{CODESIGN}: {e}"))?;
    Ok((
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    ))
}

fn first(s: &str) -> String {
    s.lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("")
        .trim()
        .to_string()
}

impl Codesign for Real {
    fn requirement_of(&self, p: &Path) -> Result<Option<String>, String> {
        let path = p.to_string_lossy();
        let (code, out, err) = run(&["-d", "-r-", &path])?;
        if code != 0 {
            return Err(format!("codesign -d -r- rc {code}: {}", first(&err)));
        }
        parse_requirement(&out)
    }
    fn verify(&self, p: &Path, requirement: &str) -> Result<(), String> {
        let path = p.to_string_lossy();
        let r = format!("-R={requirement}");
        let (code, _, err) = run(&["-v", &r, &path])?;
        if code == 0 {
            Ok(())
        } else {
            Err(format!("codesign -v rc {code}: {}", first(&err)))
        }
    }
}
