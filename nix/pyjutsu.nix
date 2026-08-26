# Reusable devenv module: Pyjutsu dev-verification entrypoints.
#
# Import it from devenv.nix:
#
#   imports = [ ./nix/pyjutsu.nix ];
#
# pytest and ruff come from the project's devenv Python venv (languages.python.venv + uv),
# resolved by their venv bin path. maturin and cargo/clippy come from PATH (nix + the rust
# toolchain). Tasks run from devenv's own CWD, so cd to the project root ($DEVENV_ROOT) first.
{ config, ... }:

let
  venvRoot = "${config.devenv.state}/venv";
  venvBin = "${config.devenv.state}/venv/bin";
in
{
  tasks = {
    # Compile the _pyjutsu native ext and install it (editable) into the devenv venv.
    "pyjutsu:build".exec = ''
      cd "$DEVENV_ROOT"
      VIRTUAL_ENV="${venvRoot}" UV_PROJECT_ENVIRONMENT="${venvRoot}" maturin develop --uv
    '';

    # Python suite (models, facade, differential tests) + Rust unit tests for the thin layer.
    "pyjutsu:test".exec = ''
      cd "$DEVENV_ROOT"
      ${venvBin}/pytest -q
      cargo test
    '';

    # ruff for Python, clippy for Rust.
    "pyjutsu:lint".exec = ''
      cd "$DEVENV_ROOT"
      ${venvBin}/ruff check python tests scripts
      cargo clippy --all-targets -- -D warnings
    '';

    # Canonical local gate. The task graph owns the build, test, and lint order.
    "pyjutsu:verify".after = [
      "pyjutsu:lint"
      "pyjutsu:test"
    ];

    # Build a release wheel into dist/, then prove it imports in a clean interpreter.
    #
    # This is a LOCAL artifact for installing pyjutsu into another project on this machine.
    # It is not a publish step: nothing here uploads, signs, or tags. `dist/` is git-ignored.
    #
    # The smoke check matters more than it looks. `maturin develop` installs an *editable*
    # build whose Python half is read from the source tree, so the whole suite can pass while
    # the packaged wheel is missing a file (`py.typed`, a new module) or carries a stale
    # extension. Installing the wheel into a throwaway venv is the only thing that exercises
    # what actually ships: it must import, report the right version, and open a real repo.
    "pyjutsu:wheel".exec = ''
      set -euo pipefail
      cd "$DEVENV_ROOT"

      rm -rf dist
      maturin build --release --out dist

      wheel="$(ls dist/*.whl)"
      echo "built $wheel"

      # A throwaway venv, deliberately NOT the devenv one: installing there would shadow the
      # editable build every later `pytest` run depends on.
      smoke="$(mktemp -d)"
      trap 'rm -rf "$smoke"' EXIT
      ${venvBin}/python -m venv "$smoke/venv"
      "$smoke/venv/bin/pip" install --quiet "$wheel"
      "$smoke/venv/bin/python" - "$wheel" <<'SMOKE'
      import pathlib, subprocess, sys, tempfile
      import pyjutsu

      expected = pathlib.Path(sys.argv[1]).name.split("-")[1]
      assert pyjutsu.__version__ == expected, (
          f"wheel is named {expected} but reports {pyjutsu.__version__}"
      )
      # The packaged extension must be the one that was just built, not a stale copy.
      assert pyjutsu.JJ_VERSION == pyjutsu.JJ_LIB_TARGET, "extension/jj-lib pin mismatch"
      # `py.typed` must survive packaging, or every consumer silently loses type information.
      assert (pathlib.Path(pyjutsu.__file__).parent / "py.typed").is_file(), "py.typed missing"

      # Open a real repo, so the smoke check exercises the native layer rather than imports.
      with tempfile.TemporaryDirectory() as tmp:
          repo = pathlib.Path(tmp) / "repo"
          repo.mkdir()
          subprocess.run(["jj", "git", "init", "--colocate", "."], cwd=repo, check=True,
                         capture_output=True)
          ws = pyjutsu.Workspace.load(repo)
          assert ws.working_copy().commit_id
      print(f"smoke check passed: pyjutsu {pyjutsu.__version__}, jj-lib {pyjutsu.JJ_VERSION}")
      SMOKE
    '';
  };

  # `devenv test` builds the ext, then runs both suites.
  enterTest = ''
    cd "$DEVENV_ROOT"
    VIRTUAL_ENV="${venvRoot}" UV_PROJECT_ENVIRONMENT="${venvRoot}" maturin develop --uv
    ${venvBin}/pytest -q
    cargo test
  '';
}
