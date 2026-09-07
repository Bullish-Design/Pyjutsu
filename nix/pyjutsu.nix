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

    # Build a portable release wheel + sdist into dist/, then prove the wheel imports in a
    # clean interpreter.
    #
    # This is a **local check and the sdist source**, not the published wheel. Vendomat builds
    # the wheel that ships (`nix build .#pyjutsu-wheel`, uploaded by `vendomat publish
    # pyjutsu`); building the shipping wheel here too is what gave one version two artifacts.
    # Nothing here uploads or tags; `dist/` is git-ignored. Run it alone to install pyjutsu
    # into another project on this machine.
    #
    # Two nix-specific corrections are needed, and both are silent when missed:
    #
    #  1. devenv exports `_PYTHON_HOST_PLATFORM=linux_x86_64`, which overrides `--compatibility`
    #     and stamps the wheel with the bare `linux_x86_64` platform tag. Unset it.
    #  2. maturin applies the manylinux tag on request but does not clean the extension, which
    #     still carries a RUNPATH into `/nix/store`. `scripts/relocate_wheel.py` strips it, so
    #     the tag states something true.
    #
    # The smoke check matters more than it looks. `maturin develop` installs an *editable*
    # build whose Python half is read from the source tree, so the whole suite can pass while
    # the packaged wheel is missing a file (`py.typed`, a new module) or carries a stale
    # extension. Installing the wheel into a throwaway venv is the only thing that exercises
    # what actually ships: it must import, report the right version, and open a real repo.
    "pyjutsu:wheel".exec = ''
      set -euo pipefail
      cd "$DEVENV_ROOT"

      unset _PYTHON_HOST_PLATFORM  # see (1) above

      rm -rf dist
      maturin build --release --compatibility manylinux_2_39 --out dist
      maturin sdist --out dist

      wheel="$(ls dist/*.whl)"
      ${venvBin}/python scripts/relocate_wheel.py "$wheel"  # see (2) above
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
      # The published wheel claims manylinux. A surviving nix-store RUNPATH would make that
      # claim false on every non-nix host, so assert the relocation actually ran. Read the
      # dynamic entry, not the raw bytes: patchelf empties the entry but leaves the now-dead
      # strings in `.dynstr`, so a byte scan reports a problem that is not there.
      ext = pathlib.Path(pyjutsu.__file__).parent / "_pyjutsu.abi3.so"
      runpath = subprocess.run(["patchelf", "--print-rpath", str(ext)],
                               capture_output=True, text=True, check=True).stdout.strip()
      assert not runpath, f"{ext.name} still carries a RUNPATH: {runpath}"

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

    # Cut the release and publish the sdist. **This task no longer publishes the wheel.**
    #
    # Vendomat is the release engine: it performs the single hermetic build and uploads that
    # exact store file with `vendomat publish pyjutsu`. Two builders uploading under one
    # version is what produced two different files carrying the same version number, and every
    # downstream symptom followed from it (gitman project 32 G3, devman 023-toolchain).
    #
    # The sdist stays here because vendomat builds no sdist, and it is the only way onto a
    # platform the manylinux wheel does not serve.
    #
    # The tag is derived from pyproject.toml, never passed in, so the tag and the artifact
    # names cannot disagree. Re-running against an existing tag fails rather than overwriting
    # a published artifact.
    "pyjutsu:publish".exec = ''
      set -euo pipefail
      cd "$DEVENV_ROOT"

      version="$(${venvBin}/python -c 'import tomllib,pathlib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
      tag="v$version"

      if [ -n "$(git status --porcelain)" ]; then
        echo "refusing to publish: working tree is dirty. Commit or stash first." >&2
        exit 1
      fi
      if gh release view "$tag" >/dev/null 2>&1; then
        echo "refusing to publish: release $tag already exists. Bump the version first." >&2
        exit 1
      fi

      # Name the sdist from the version rather than globbing. `dist/` is not cleaned between
      # releases, so a glob matches every stale artifact still sitting there.
      sdist="dist/pyjutsu-$version.tar.gz"
      [ -f "$sdist" ] || {
        echo "no $sdist — run \`devenv tasks run pyjutsu:wheel\` first." >&2
        exit 1
      }

      # The tag may already exist: `gitman release` writes and pushes it, and this task then
      # only attaches the artifacts. Create it only when it is missing, so re-publishing a
      # release whose tag is already public never moves it.
      if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
        tagged="$(git rev-list -n1 "$tag")"
        if [ "$tagged" != "$(git rev-parse HEAD)" ]; then
          echo "note: $tag points at $tagged but HEAD is $(git rev-parse HEAD);" >&2
          echo "      the artifacts were built from HEAD. Check the difference is not in the" >&2
          echo "      library before continuing." >&2
        fi
      else
        git tag -a "$tag" -m "Release $version"
      fi
      git push origin "$tag"
      gh release create "$tag" "$sdist" \
        --title "pyjutsu $version" \
        --notes "pyjutsu $version — in-process binding to jj-lib.

Pyjutsu is not on PyPI. Install it from this release by pinning the wheel in your
\`pyproject.toml\`:

    [project]
    dependencies = [\"pyjutsu==$version\"]

    [tool.uv.sources]
    pyjutsu = { url = \"https://github.com/Bullish-Design/Pyjutsu/releases/download/$tag/pyjutsu-$version-cp313-abi3-manylinux_2_39_x86_64.whl\" }

The wheel is abi3 (one build serves CPython 3.13 and later) and manylinux_2_39, so it needs
glibc 2.39 or newer on x86-64 Linux. On any other platform, build from the sdist in this
release; that needs a Rust toolchain."
      echo "published $tag with the sdist."
      echo "Now attach the wheel from the store:  vendomat publish pyjutsu"
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
