"""Pyjutsu exception hierarchy.

The whole taxonomy is defined in the native `_pyjutsu` extension (concept §8.2) so the Rust
layer raises the precise subclass when it maps a `jj-lib` error. This module re-exports them
so callers can `from pyjutsu.errors import RevsetError` without reaching into the extension.
"""

from __future__ import annotations

from ._pyjutsu import (
    BackendError,
    ConflictError,
    GitError,
    ImmutableCommitError,
    PartialWorkspaceError,
    PyjutsuError,
    RevsetError,
    StaleWorkingCopyError,
    WorkingCopyError,
    WorkspaceError,
)

__all__ = [
    "PyjutsuError",
    "RevsetError",
    "ConflictError",
    "BackendError",
    "WorkspaceError",
    "PartialWorkspaceError",
    "WorkingCopyError",
    "StaleWorkingCopyError",
    "ImmutableCommitError",
    "GitError",
    "JjCliError",
    "HookAbort",
    "PostHookError",
    "PublishError",
    "StalePublishError",
    "PublishIncompleteError",
    "STALE_REASONS",
]

#: Reason codes of :class:`StalePublishError`. Each one means the call changed nothing visible.
STALE_REASONS = (
    "commit-moved",
    "dirty-working-copy",
    "stale-working-copy",
    "op-heads-moved",
)


class JjCliError(PyjutsuError):
    """The ``jj`` subprocess invoked by :meth:`pyjutsu.Workspace.run_jj` failed.

    Raised **only** by the ``run_jj`` escape hatch (never by the in-process typed surface): when the
    ``jj`` binary can't be found, or — under ``check=True`` — when it exits non-zero. Defined in pure
    Python (unlike the rest of the hierarchy, which the native layer raises) since the escape hatch
    is pure Python too.

    Attributes:
        command: the ``jj`` args that were run (without the leading ``jj``).
        returncode: the process exit code, or ``None`` if ``jj`` could not be launched.
        stdout: captured standard output (empty if ``jj`` could not be launched).
        stderr: captured standard error (or the launch error message).
    """

    def __init__(
        self,
        message: str,
        *,
        command: list[str],
        returncode: int | None,
        stdout: str,
        stderr: str,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class HookAbort(PyjutsuError):
    """A pre-hook vetoed (or failed) before its operation ran.

    Raised by the hook machinery when a registered ``pre-*`` hook raises: either the hook raises
    :class:`HookAbort` itself, or any other exception is wrapped in one (fail-closed, like git).
    For a transaction the pending transaction is rolled back (nothing is published) and the error
    propagates out of the ``with`` block; for a git verb (:meth:`~pyjutsu.Workspace.git_push`,
    :meth:`~pyjutsu.Workspace.git_fetch`) the operation is never started.
    """


class PostHookError(PyjutsuError):
    """A ``post-*`` hook failed *after* its operation was published.

    The operation is already in the op log (or, for git verbs, already on the remote) — this error
    says *the hook* failed, not the operation. Carries the published operation id so the caller
    can act on the landed op; a transaction that raised this did commit.

    Attributes:
        operation_id: the id of the published operation, or ``None`` if the event published none
            (e.g. a push that changed nothing).
    """

    def __init__(self, operation_id: str | None, message: str) -> None:
        super().__init__(message)
        self.operation_id = operation_id


#: Reason code per :class:`PublishIncompleteError` stage.
_INCOMPLETE_REASONS = {
    "checkout": "published-checkout-failed",
    "git-sync": "published-git-sync-failed",
    "publish-uncertain": "publish-uncertain",
}


class PublishError(PyjutsuError):
    """:meth:`pyjutsu.Workspace.publish_if` refused or failed.

    This base class covers a refusal that changed nothing and is not a stale finding: an invalid
    commit id, an ``onto`` commit that does not exist, or an unsupported store. Its subclasses
    cover a stale finding (:class:`StalePublishError`) and a landed or uncertain operation
    (:class:`PublishIncompleteError`).

    Attributes:
        reason: a stable code, for example ``"onto-not-found"`` or ``"unsupported-store"``.
        expected_wc_commit: the working-copy commit id the caller expected.
        onto: the ``onto`` commit id the caller passed.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        expected_wc_commit: str | None = None,
        onto: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.expected_wc_commit = expected_wc_commit
        self.onto = onto


class StalePublishError(PublishError):
    """The precondition failed. The call published nothing and moved neither ``@`` nor any file.

    Attributes:
        reason: one of :data:`STALE_REASONS`.
        observed_wc_commit: the working-copy commit id the call read inside the lock.
        head_operations: the operation heads the call saw. More than one means divergent heads.
        dirty: ``True`` when the disk holds changes the working-copy commit does not. The call
            left those bytes on disk.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        expected_wc_commit: str | None,
        onto: str | None,
        observed_wc_commit: str,
        head_operations: list[str],
        dirty: bool,
    ) -> None:
        super().__init__(message, reason=reason, expected_wc_commit=expected_wc_commit, onto=onto)
        self.observed_wc_commit = observed_wc_commit
        self.head_operations = head_operations
        self.dirty = dirty


class PublishIncompleteError(PublishError):
    """The operation landed or may have landed, but a later step failed.

    Never treat this as a stale refusal. Read :attr:`recovery` and run it.

    Attributes:
        stage: ``"checkout"`` (the working copy is stale), ``"git-sync"`` (the working copy is
            current and Git ``HEAD`` or the index lags), or ``"publish-uncertain"`` (the head
            update failed and the operation log may or may not hold ``operation``).
        operation: the publication operation id.
        recovery: the recovery action, as one sentence.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        operation: str,
        recovery: str,
        expected_wc_commit: str | None,
        onto: str | None,
    ) -> None:
        super().__init__(
            message,
            reason=_INCOMPLETE_REASONS.get(stage, stage),
            expected_wc_commit=expected_wc_commit,
            onto=onto,
        )
        self.stage = stage
        self.operation = operation
        self.recovery = recovery
