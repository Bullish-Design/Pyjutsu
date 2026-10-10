# Pyjutsu as a Nix-built Python package: a mixed Python and Rust (PyO3) wheel from maturin.
#
# The cargo dependencies come from `Cargo.lock` through `importCargoLock`. Every package
# in the lock file comes from crates.io, so the build needs no hash.
#
# The build does not run the test suite. The suite shells out to the real `jj` 0.44.0 binary
# as an oracle. The repository gate (`pyjutsu:verify`) runs it. The install check runs a
# smoke command instead:
#   - `pyjutsu --help` exits 0;
#   - the extension reports the same version as the package;
#   - the release build carries no test hooks.
#
# `.cargo/config.toml` selects the `mold` linker for development. It is not in the source
# set, so this build uses the default linker.
{ lib
, python3Packages
, rustPlatform
}:

let
  pyproject = builtins.fromTOML (builtins.readFile ../pyproject.toml);
in
python3Packages.buildPythonPackage {
  pname = "pyjutsu";
  version = pyproject.project.version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../python
      ../src
      ../Cargo.toml
      ../Cargo.lock
      ../build.rs
      ../pyproject.toml
      ../README.md
      ../LICENSE
    ];
  };

  cargoDeps = rustPlatform.importCargoLock { lockFile = ../Cargo.lock; };

  nativeBuildInputs = [
    rustPlatform.cargoSetupHook
    rustPlatform.maturinBuildHook
  ];

  dependencies = [ python3Packages.pydantic ];

  pythonImportsCheck = [ "pyjutsu" ];

  doInstallCheck = true;
  postInstallCheck = ''
    export HOME=$TMPDIR
    $out/bin/pyjutsu --help > /dev/null
    PYTHONPATH="$out/${python3Packages.python.sitePackages}:$PYTHONPATH" \
      ${python3Packages.python.withPackages (ps: [ ps.pydantic ])}/bin/python - <<'SMOKE'
    import pyjutsu
    from pyjutsu import _pyjutsu
    assert _pyjutsu.pyjutsu_version() == pyjutsu.__version__, "extension and package differ"
    assert pyjutsu.JJ_VERSION == pyjutsu.JJ_LIB_TARGET
    assert not _pyjutsu.has_test_hooks(), "the build carries the test-only publication hooks"
    print("pyjutsu", pyjutsu.__version__, "jj-lib", pyjutsu.JJ_VERSION)
    SMOKE
  '';

  meta = {
    description = pyproject.project.description;
    homepage = "https://github.com/Bullish-Design/Pyjutsu";
    license = lib.licenses.mit;
    mainProgram = "pyjutsu";
  };
}
