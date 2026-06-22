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
