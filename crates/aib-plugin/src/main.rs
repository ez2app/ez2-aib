//! `aib-plugin`: the plugin area from the command line.
//!
//! ```text
//! aib-plugin list [--json]             every plugin found, its switch, and whether its first command would run
//! aib-plugin on <name> | off <name>    set the switch (the plugin must be installed)
//! aib-plugin run <name> [<command>] -- <args>...   run a plugin's command (its first when not named)
//! ```
//! Exit: `run` gives the command's own code; 3 = not run (the line says why); 2 = a bad call
//! or the plugin area could not be read; `list` is 0 when it could list.

use aib_plugin::{find, home, plugins_dir, read_switches, runnable, switched_on, switches_file};
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "usage: aib-plugin list [--json]\n       aib-plugin on <name> | off <name>\n       aib-plugin run <name> [<command>] -- <args>...";

fn aib_home() -> Option<PathBuf> {
    home(
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .as_deref(),
    )
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(h) = aib_home() else {
        eprintln!("aib-plugin: no home folder is known");
        return ExitCode::from(2);
    };
    let dir = plugins_dir(&h);
    let found = match find(&dir) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("aib-plugin: {e}");
            return ExitCode::from(2);
        }
    };
    let sw_file = switches_file(&h);
    let switches = read_switches(&sw_file);
    let windows = cfg!(windows);
    match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["list"] | ["list", "--json"] => {
            let json = args.len() == 2;
            let mut rows = Vec::new();
            for f in &found {
                let folder = dir.join(&f.folder);
                let row = match &f.manifest {
                    Err(e) => serde_json::json!({"name": f.folder, "ok": false, "why": e}),
                    Ok(m) => {
                        let on = switches.as_ref().is_ok_and(|s| switched_on(m, s));
                        let first = &m.commands[0].name;
                        let run = runnable(m, &folder, &switches, first, windows);
                        serde_json::json!({
                            "name": m.name, "ok": true, "version": m.version, "summary": m.summary,
                            "paid": m.paid, "switch": if on { "on" } else { "off" },
                            "commands": m.commands.iter().map(|c| c.name.clone()).collect::<Vec<_>>(),
                            "runs": run.is_ok(),
                            "why_not": run.as_ref().err().map(|n| n.word()),
                            "why_not_text": run.as_ref().err().map(|n| n.text()),
                        })
                    }
                };
                rows.push(row);
            }
            let stray = switches
                .as_ref()
                .map(|s| aib_plugin::stray_switches(s, &found))
                .unwrap_or_default();
            if json {
                println!(
                    "{}",
                    serde_json::json!({"v": 1, "plugins": rows, "stray_switches": stray,
                        "switches_unread": switches.as_ref().err()})
                );
            } else if rows.is_empty() {
                println!("no plugin under {}", dir.display());
            } else {
                for r in &rows {
                    if r["ok"] == false {
                        println!(
                            "{:<20} NOT READ  {}",
                            r["name"].as_str().unwrap_or(""),
                            r["why"].as_str().unwrap_or("")
                        );
                    } else {
                        println!(
                            "{:<20} {:<9} {:<3}  {}  {}",
                            r["name"].as_str().unwrap_or(""),
                            r["version"].as_str().unwrap_or(""),
                            r["switch"].as_str().unwrap_or(""),
                            if r["runs"] == true {
                                "runs".to_string()
                            } else {
                                format!("not run: {}", r["why_not_text"].as_str().unwrap_or(""))
                            },
                            r["summary"].as_str().unwrap_or("")
                        );
                    }
                }
            }
            if !json {
                for s in &stray {
                    println!("a switch is set for {s}, which is not an installed plugin: it switches nothing");
                }
                if let Err(e) = &switches {
                    println!("the switches cannot be read ({e}): no plugin is taken as on");
                }
            }
            ExitCode::SUCCESS
        }
        [word @ ("on" | "off"), name] => {
            if !found
                .iter()
                .any(|f| f.manifest.as_ref().is_ok_and(|m| m.name == *name))
            {
                eprintln!("aib-plugin: no plugin {name} is installed and readable");
                return ExitCode::from(2);
            }
            if let Err(e) = aib_plugin::set_switch(&sw_file, name, *word == "on") {
                eprintln!("aib-plugin: {e}");
                return ExitCode::from(2);
            }
            println!("{name} {word}");
            ExitCode::SUCCESS
        }
        ["run", name, rest @ ..] => {
            let (command, cmd_args): (Option<&str>, &[&str]) = match rest {
                ["--", a @ ..] => (None, a),
                [c, "--", a @ ..] => (Some(c), a),
                _ => {
                    eprintln!("{USAGE}");
                    return ExitCode::from(2);
                }
            };
            let Some(f) = found.iter().find(|f| f.folder == *name) else {
                println!(
                    "aib-plugin: {name} not run: no_such_plugin (nothing under {})",
                    dir.join(name).display()
                );
                return ExitCode::from(3);
            };
            let m = match &f.manifest {
                Ok(m) => m,
                Err(e) => {
                    println!("aib-plugin: {name} not run: not_read ({e})");
                    return ExitCode::from(3);
                }
            };
            let command = command.unwrap_or(&m.commands[0].name);
            let exe = match runnable(m, &dir.join(&f.folder), &switches, command, windows) {
                Ok(e) => e,
                Err(no) => {
                    println!("aib-plugin: {name} not run: {} ({})", no.word(), no.text());
                    return ExitCode::from(3);
                }
            };
            match std::process::Command::new(&exe).args(cmd_args).status() {
                // the command's own code, whole (Windows codes go past 255)
                Ok(st) => std::process::exit(st.code().unwrap_or(1)),
                Err(e) => {
                    eprintln!("aib-plugin: {}: {e}", exe.display());
                    ExitCode::from(2)
                }
            }
        }
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}
