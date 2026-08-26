"""C8: commit signing (lane `003/c8`).

Signing is configured through jj's own ``signing.*`` keys — Pyjutsu adds none. The end-to-end
tests use the SSH backend, which needs `ssh-keygen`; they skip when it is absent. The oracle for
a signature's presence and verdict is the pinned `jj` CLI's `signature`/`signature.status`
templates.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pyjutsu
import pytest

from tests.diff.jj_cli import JjCli

#: The identity the shared test config authors with.
_EMAIL = "test@pyjutsu.invalid"

_HAS_SSH_KEYGEN = shutil.which("ssh-keygen") is not None
requires_ssh_keygen = pytest.mark.skipif(
    not _HAS_SSH_KEYGEN, reason="ssh-keygen is not installed"
)


def _ssh_signing_config(tmp_path: Path, behavior: str) -> str:
    """Generate a key pair and return the jj config enabling the SSH signing backend."""
    key = tmp_path / "signing_key"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", _EMAIL, "-f", str(key)],
        check=True,
        capture_output=True,
    )
    public = key.with_suffix(".pub").read_text().strip()
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"{_EMAIL} {public}\n")
    return (
        "[signing]\n"
        'backend = "ssh"\n'
        f'behavior = "{behavior}"\n'
        f'key = "{key.with_suffix(".pub")}"\n'
        "\n[signing.backends.ssh]\n"
        f'allowed-signers = "{allowed}"\n'
    )


def _repo(tmp_path: Path, jj: JjCli, name: str = "signing") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    jj.init_colocated(repo)
    (repo / "a.txt").write_text("alpha\n")
    jj(repo, "describe", "-m", "commit A")
    jj(repo, "new")
    return repo


def _cli_is_signed(jj: JjCli, repo: Path, revset: str) -> bool:
    return jj.template(repo, revset, 'if(signature, "true", "false")') == "true"


def test_unsigned_commit_reads_as_unsigned(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    view = ws.head()
    assert view.resolve("@-").is_signed is False
    assert view.verify("@-") is None
    # The CLI agrees.
    assert _cli_is_signed(jj, repo, "@-") is False


@requires_ssh_keygen
def test_pyjutsu_signs_what_it_writes(tmp_path: Path, jj: JjCli) -> None:
    """With `signing.behavior = "own"`, a commit written through the binding carries a signature."""
    jj.append_config(_ssh_signing_config(tmp_path, "own"))
    repo = _repo(tmp_path, jj)

    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("describe") as tx:
        tx.describe("@", "signed by pyjutsu")

    view = ws.head()
    commit = view.resolve("@")
    assert commit.is_signed is True
    assert _cli_is_signed(jj, repo, "@") is True

    verdict = view.verify("@")
    assert verdict is not None
    assert verdict.status == "good"
    # The SSH backend reports the key fingerprint, and the allowed-signers principal as display.
    assert verdict.key is not None and verdict.key.startswith("SHA256:")
    assert verdict.display == _EMAIL
    # The CLI's own verdict matches.
    assert jj.template(repo, "@", "signature.status()") == "good"


@requires_ssh_keygen
def test_sign_behavior_drop_overrides_the_configured_behavior(tmp_path: Path, jj: JjCli) -> None:
    """`sign_behavior="drop"` at load time beats `signing.behavior = "own"` in the config."""
    jj.append_config(_ssh_signing_config(tmp_path, "own"))
    repo = _repo(tmp_path, jj)

    ws = pyjutsu.Workspace.load(repo, sign_behavior="drop")
    with ws.transaction("describe") as tx:
        tx.describe("@", "not signed")

    assert ws.head().resolve("@").is_signed is False
    assert _cli_is_signed(jj, repo, "@") is False


@requires_ssh_keygen
def test_sign_behavior_force_overrides_a_dropping_config(tmp_path: Path, jj: JjCli) -> None:
    jj.append_config(_ssh_signing_config(tmp_path, "drop"))
    repo = _repo(tmp_path, jj)

    ws = pyjutsu.Workspace.load(repo, sign_behavior="force")
    with ws.transaction("describe") as tx:
        tx.describe("@", "signed anyway")

    assert ws.head().resolve("@").is_signed is True


@requires_ssh_keygen
def test_keep_preserves_a_signature_across_a_rewrite(tmp_path: Path, jj: JjCli) -> None:
    """jj's default behaviour: rewriting your own signed commit re-signs it."""
    jj.append_config(_ssh_signing_config(tmp_path, "own"))
    repo = _repo(tmp_path, jj)
    jj(repo, "describe", "-m", "signed by the CLI")
    assert _cli_is_signed(jj, repo, "@") is True

    ws = pyjutsu.Workspace.load(repo, sign_behavior="keep")
    with ws.transaction("re-describe") as tx:
        tx.describe("@", "rewritten")

    assert ws.head().resolve("@").is_signed is True


@requires_ssh_keygen
def test_verify_reports_unknown_without_allowed_signers(tmp_path: Path, jj: JjCli) -> None:
    """A valid signature the backend cannot attribute verifies as `unknown`, not `bad`."""
    config = _ssh_signing_config(tmp_path, "own")
    # Drop the allowed-signers file, keeping the rest.
    config = config.split("\n[signing.backends.ssh]")[0]
    jj.append_config(config)
    repo = _repo(tmp_path, jj)

    ws = pyjutsu.Workspace.load(repo)
    with ws.transaction("describe") as tx:
        tx.describe("@", "signed, unattributable")

    verdict = ws.head().verify("@")
    assert verdict is not None
    assert verdict.status == "unknown"
    assert jj.template(repo, "@", "signature.status()") == "unknown"


def test_invalid_sign_behavior_raises(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo(tmp_path, jj)
    with pytest.raises(pyjutsu.WorkspaceError, match="invalid sign_behavior"):
        pyjutsu.Workspace.load(repo, sign_behavior="sometimes")


def test_verify_requires_a_single_revision(tmp_path: Path, jj: JjCli) -> None:
    repo = _repo(tmp_path, jj)
    ws = pyjutsu.Workspace.load(repo)
    with pytest.raises(pyjutsu.RevsetError):
        ws.head().verify("all()")
