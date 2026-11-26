//! The measured table (IFACE-provider-lifecycle-c2f0b-1126 §4, r1-2): the one place a
//! provider's version, download address, SHA-256 and size come from. Installing, changing
//! version and the version gate read only this table; a version not listed here is never
//! offered (Andy 11-26 02:3x: AIB leads the updates, an unmeasured version is not offered).
//!
//! Each row was copied from the provider's release manifest when it was measured
//! (EVIDENCE-measured-<provider>-<version>.txt keeps the manifest's own hash and how it was
//! checked). The hash here is what a download is held to; the manifest is not read again.

/// One measured build: what L1 (`provider_fetch`) downloads and checks, nothing else.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Measured {
    pub provider: &'static str,
    pub version: &'static str,
    /// `darwin-arm64`, `darwin-x64`, `win32-x64` or `win32-arm64`.
    pub platform: &'static str,
    /// The full https address, fixed here; never taken from a request, a page or a file.
    pub url: &'static str,
    /// Lowercase hex SHA-256 of the whole file.
    pub sha256: &'static str,
    /// Bytes.
    pub size: u64,
}

/// The platforms a row may name.
pub const PLATFORMS: [&str; 4] = ["darwin-arm64", "darwin-x64", "win32-x64", "win32-arm64"];

/// Claude Code stable 2.1.326 (manifest sha256 883e153b…be1e, 11-26 03:2x;
/// EVIDENCE-measured-claude-2.1.326.txt).
pub const MEASURED: &[Measured] = &[
    Measured {
        provider: "claude",
        version: "2.1.326",
        platform: "darwin-arm64",
        url: "https://downloads.claude.ai/claude-code-releases/2.1.326/darwin-arm64/claude",
        sha256: "e214ad76dcc0e846b03be28ed1c2734aacd4e27acb527cb7f8a9e2bed644e837",
        size: 353_037_664,
    },
    Measured {
        provider: "claude",
        version: "2.1.326",
        platform: "darwin-x64",
        url: "https://downloads.claude.ai/claude-code-releases/2.1.326/darwin-x64/claude",
        sha256: "bd8e26290f93353590b831600a7ced02d625018fbaa2a82d8817368d3e2d2f82",
        size: 367_809_664,
    },
    Measured {
        provider: "claude",
        version: "2.1.326",
        platform: "win32-x64",
        url: "https://downloads.claude.ai/claude-code-releases/2.1.326/win32-x64/claude.exe",
        sha256: "44e85ac6d3e36b924a0d631d3f69c13a6907491115d71e97c8eb6160c82b2aed",
        size: 378_067_616,
    },
    Measured {
        provider: "claude",
        version: "2.1.326",
        platform: "win32-arm64",
        url: "https://downloads.claude.ai/claude-code-releases/2.1.326/win32-arm64/claude.exe",
        sha256: "12042740540c184b351274c11aa808252796770626ce990be740dd0d11d67e5b",
        size: 360_077_472,
    },
];

/// The platform this build runs on, as the table names it; `None` where no row can apply.
pub fn this_platform() -> Option<&'static str> {
    platform_of(std::env::consts::OS, std::env::consts::ARCH)
}

/// [`this_platform`] for a given OS and architecture (tests name both).
pub fn platform_of(os: &str, arch: &str) -> Option<&'static str> {
    match (os, arch) {
        ("macos", "aarch64") => Some("darwin-arm64"),
        ("macos", "x86_64") => Some("darwin-x64"),
        ("windows", "x86_64") => Some("win32-x64"),
        ("windows", "aarch64") => Some("win32-arm64"),
        _ => None,
    }
}

/// The measured row for `provider` on `platform`, if any.
pub fn measured_for(provider: &str, platform: &str) -> Option<&'static Measured> {
    MEASURED
        .iter()
        .find(|m| m.provider == provider && m.platform == platform)
}

/// Is `out` (what `claude --version` printed, `2.1.326 (Claude Code)`) a measured version?
pub fn claude_version_ok(out: &str) -> bool {
    out.lines().any(|l| {
        let l = l.trim();
        MEASURED.iter().any(|m| {
            m.provider == "claude"
                && l.strip_prefix(m.version)
                    .is_some_and(|r| r.is_empty() || r.starts_with(' '))
        })
    })
}

/// Why a row is not well formed (the table's own check; a test runs it on every row).
pub fn row_fault(m: &Measured) -> Option<&'static str> {
    let hex = m.sha256.len() == 64
        && m.sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    let version = {
        let parts: Vec<&str> = m.version.split('.').collect();
        parts.len() == 3
            && parts
                .iter()
                .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
    };
    let url_ok = match m.provider {
        "claude" => {
            let exe = if m.platform.starts_with("win32") {
                "claude.exe"
            } else {
                "claude"
            };
            m.url
                == format!(
                    "https://downloads.claude.ai/claude-code-releases/{}/{}/{exe}",
                    m.version, m.platform
                )
        }
        _ => false,
    };
    if !hex {
        Some("sha256_not_lowercase_hex_64")
    } else if !version {
        Some("version_not_x_y_z")
    } else if !PLATFORMS.contains(&m.platform) {
        Some("platform_unknown")
    } else if m.size == 0 || m.size > 600_000_000 {
        Some("size_out_of_range")
    } else if !url_ok {
        Some("url_not_the_providers_fixed_one")
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_measured_row_is_well_formed_and_none_repeats() {
        for m in MEASURED {
            assert_eq!(row_fault(m), None, "reason: bad_row {m:?}");
            let same = MEASURED
                .iter()
                .filter(|o| o.provider == m.provider && o.platform == m.platform)
                .count();
            assert_eq!(
                same, 1,
                "reason: two_rows_for {} {}",
                m.provider, m.platform
            );
        }
        for p in PLATFORMS {
            assert!(
                measured_for("claude", p).is_some(),
                "reason: claude_missing_platform {p}"
            );
        }
        // the check itself refuses a row that is off in each way
        let good = MEASURED[0];
        let mut bad = good;
        bad.sha256 = "E214AD76DCC0E846B03BE28ED1C2734AACD4E27ACB527CB7F8A9E2BED644E837";
        assert!(row_fault(&bad).is_some());
        let mut bad = good;
        bad.url = "https://example.com/claude";
        assert!(row_fault(&bad).is_some());
        let mut bad = good;
        bad.version = "2.1";
        assert!(row_fault(&bad).is_some());
        let mut bad = good;
        bad.size = 0;
        assert!(row_fault(&bad).is_some());
    }

    #[test]
    fn only_a_measured_claude_version_passes() {
        assert!(claude_version_ok("2.1.326 (Claude Code)\n"));
        assert!(claude_version_ok("2.1.326"));
        for v in [
            "2.1.332 (Claude Code)",
            "2.1.3260",
            "2.1.32",
            "",
            "claude 2.1.326",
        ] {
            assert!(!claude_version_ok(v), "reason: unmeasured_version_ok {v:?}");
        }
    }

    #[test]
    fn the_platform_is_named_as_the_table_names_it() {
        assert_eq!(platform_of("windows", "x86_64"), Some("win32-x64"));
        assert_eq!(platform_of("macos", "aarch64"), Some("darwin-arm64"));
        assert_eq!(platform_of("linux", "x86_64"), None);
    }
}
