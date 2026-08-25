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
  };

  # `devenv test` builds the ext, then runs both suites.
  enterTest = ''
    cd "$DEVENV_ROOT"
    VIRTUAL_ENV="${venvRoot}" UV_PROJECT_ENVIRONMENT="${venvRoot}" maturin develop --uv
    ${venvBin}/pytest -q
    cargo test
  '';
}
