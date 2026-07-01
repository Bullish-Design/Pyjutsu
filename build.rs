//! Build script: derive the *resolved* `jj-lib` version from the committed `Cargo.lock` and hand
//! it to the crate as `PYJUTSU_JJ_LIB_VERSION` (read via `env!` in `src/lib.rs`).
//!
//! This makes the jj-lib version a **build-derived fact** rather than a hand-maintained string:
//! `_pyjutsu.version()` then reports the dependency actually linked, so it can never drift from the
//! Cargo pin (project 10 §P3). Parsing `Cargo.lock` (already present next to `Cargo.toml`, no
//! network, no `cargo metadata` subprocess) keeps this robust under the nix/devenv build sandbox.
//!
//! Fails **loudly** (panics → build error) if the lock file is missing or has no `jj-lib` entry —
//! a silent fallback here would reintroduce exactly the drift this script exists to prevent.

use std::fs;
use std::path::Path;

fn main() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")
        .expect("CARGO_MANIFEST_DIR is always set by cargo for build scripts");
    let lock_path = Path::new(&manifest_dir).join("Cargo.lock");
    // Re-run if the lock changes (a jj-lib bump) or if this script itself changes.
    println!("cargo:rerun-if-changed=Cargo.lock");
    println!("cargo:rerun-if-changed=build.rs");

    let lock = fs::read_to_string(&lock_path).unwrap_or_else(|e| {
        panic!(
            "build.rs: could not read {} to derive the jj-lib version: {e}",
            lock_path.display()
        )
    });
    let version = jj_lib_version(&lock).unwrap_or_else(|| {
        panic!(
            "build.rs: no `jj-lib` package found in {}; the committed lock file must resolve the \
             jj-lib dependency",
            lock_path.display()
        )
    });
    println!("cargo:rustc-env=PYJUTSU_JJ_LIB_VERSION={version}");
}

/// Extract the resolved `jj-lib` version from a `Cargo.lock`. Scans the `[[package]]` blocks for the
/// one whose `name = "jj-lib"` and returns its `version` value. Returns `None` if absent.
fn jj_lib_version(lock: &str) -> Option<String> {
    let mut is_jj_lib = false;
    for line in lock.lines() {
        let line = line.trim();
        if line == "[[package]]" {
            is_jj_lib = false; // entering a fresh block; reset until we see its name
        } else if let Some(rest) = line.strip_prefix("name = ") {
            is_jj_lib = rest.trim().trim_matches('"') == "jj-lib";
        } else if is_jj_lib && let Some(rest) = line.strip_prefix("version = ") {
            return Some(rest.trim().trim_matches('"').to_owned());
        }
    }
    None
}
