{ pkgs, lib, config, inputs, ... }:

let
  # jujutsu pinned to 0.42.0 from a dedicated nixpkgs input (see devenv.yaml). This is the
  # CLI of the exact jj-lib Pyjutsu binds; differential tests run it side-by-side with the
  # binding. A future jj bump is a deliberate Rust-side port + a Pyjutsu minor bump.
  jjPkgs = import inputs.nixpkgs-jj { system = pkgs.stdenv.system; };
in
{
  # Dev verification tasks (pyjutsu:build/test/lint) + enterTest.
  imports = [ ./nix/pyjutsu.nix ];

  # https://devenv.sh/basics/
  env.PROJ = "pyjutsu";

  # https://devenv.sh/packages/
  packages = [
    pkgs.git
    pkgs.uv
    pkgs.maturin
    jjPkgs.jujutsu
    # Fast linker for the native extension. `_pyjutsu` is one crate statically linked against the
    # whole of jj-lib, so every incremental rebuild re-links a large cdylib — `mold` cuts that link
    # step from many seconds to ~1s. Wired up as the linker in `.cargo/config.toml`.
    pkgs.mold
  ];

  # https://devenv.sh/languages/
  # Rust toolchain for the _pyjutsu native extension. jj-lib 0.42.0 requires Rust >= 1.89
  # (edition 2024); rolling nixpkgs' stable rustc satisfies this. (A specific `channel` would
  # pull in the rust-overlay input; the nixpkgs toolchain is enough here.)
  languages.rust.enable = true;

  languages.python = {
    enable = true;
    version = "3.13";
    venv.enable = true;
    uv = {
      enable = true;
      # Sync the dev deps (pydantic, pytest, ruff) into the venv on shell entry so the
      # Python layer + tooling resolve from the venv. maturin itself comes from nix above.
      sync.enable = true;
    };
  };

  # devman — the automation plane (CONCEPT.md §5).
  #
  # `base` alone. The `python` group puts a `typecheck` step between lint and
  # test, and this project has no type checker: its Python layer is thin and the
  # checking that matters is clippy over the Rust crate, which is already inside
  # `pyjutsu:lint`.
  devman = {
    enable = true;
    project = "pyjutsu";
    groups = [ "base" ];
  };

  # base's two names, aliased onto the entrypoints ./nix/pyjutsu.nix already
  # defines. A devenv task with only `after` and no `exec` runs its dependency
  # and fails when that dependency fails, so this duplicates no command bodies.
  tasks = {
    "base:check".after = [ "pyjutsu:lint" ];
    "base:test".after = [ "pyjutsu:test" ];

    # The native extension must exist before the Python suite imports it, and
    # `enterTest` above already encodes that order by running `maturin develop`
    # first. Stating it as a task dependency puts the ordering in ONE place:
    # CONCEPT.md §6 — "a repo with genuinely internal ordering, build before
    # test, expresses it as a devenv task dependency and exposes one task", and
    # "never write the same dependency graph in both". Without this line
    # `devenv tasks run pyjutsu:test` runs pytest against whatever was last
    # built, which is the drift that rule exists to prevent.
    "pyjutsu:test".after = [ "pyjutsu:build" ];
  };

  enterShell = ''
    # Only announce in an interactive terminal; stay silent when a command captures stdout
    # (e.g. an agent running `devenv shell -- python -c ...`).
    if [ -t 1 ]; then
      echo "pyjutsu devenv"
      jj --version
      rustc --version
      git --version
    fi
  '';

  # Dev verification tasks + enterTest are provided by ./nix/pyjutsu.nix (imported above).

  # See full reference at https://devenv.sh/reference/options/
}
