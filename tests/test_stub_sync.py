"""The `_pyjutsu.pyi` drift guard.

`tests/golden/model_fields.json` guards the shape of the Pydantic **models**. Nothing guarded
the shape of the **native** surface, so a `#[pymethods]` addition could ship without its stub
entry — and one did: `PyTransaction.changed_paths` was missing from `_pyjutsu.pyi` from 0.14.0
until 0.19.0. The stub is hand-maintained and invisible at runtime, so only a test can hold it
to the compiled extension.

The comparison is by **name**, not signature: the stub's parameter types are hand-written
documentation that no runtime introspection can verify (a PyO3 method exposes no typed
signature). Names are what drift silently; a wrong type annotation is at least visible to a
reader of the stub.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pyjutsu import _pyjutsu as ext

STUB = Path(__file__).parent.parent / "python" / "pyjutsu" / "_pyjutsu.pyi"


def _stub_module() -> ast.Module:
    return ast.parse(STUB.read_text(), filename=str(STUB))


def _public(names: object) -> set[str]:
    """Public, non-dunder names.

    Dunders are excluded on both sides: `PyCommitStream.__iter__`/`__next__` are protocol
    methods whose absence would break every iteration test immediately, so they need no
    static guard.
    """
    return {n for n in names if not n.startswith("_")}  # type: ignore[union-attr]


def _stub_handle_classes() -> dict[str, set[str]]:
    """`{class name: declared method names}` for the stub's handle classes.

    A handle class is one the stub declares with no base. The exception classes all carry a
    base (`Exception`, or another error), declare no methods, and inherit `args`/`add_note`/
    `with_traceback` from `BaseException` — comparing those would be noise.
    """
    return {
        node.name: _public({n.name for n in node.body if isinstance(n, ast.FunctionDef)})
        for node in _stub_module().body
        if isinstance(node, ast.ClassDef) and not node.bases
    }


def _stub_functions() -> set[str]:
    return _public({n.name for n in _stub_module().body if isinstance(n, ast.FunctionDef)})


def _extension_methods(cls: type) -> set[str]:
    """The class's **own** public methods.

    `vars()`, not `dir()`: `dir()` would pull in inherited object/BaseException members.
    """
    return _public(vars(cls).keys())


HANDLE_CLASSES = sorted(_stub_handle_classes())


def test_the_stub_declares_the_handle_classes() -> None:
    """A pyclass registered on the module must appear in the stub at all."""
    registered = {
        name
        for name, value in vars(ext).items()
        if isinstance(value, type) and not issubclass(value, BaseException)
    }
    assert _public(registered) == set(HANDLE_CLASSES)


@pytest.mark.parametrize("class_name", HANDLE_CLASSES)
def test_stub_methods_match_the_extension(class_name: str) -> None:
    cls = getattr(ext, class_name)
    declared = _stub_handle_classes()[class_name]
    actual = _extension_methods(cls)

    missing = sorted(actual - declared)
    assert not missing, (
        f"{class_name}: these native methods are missing from _pyjutsu.pyi: {missing}. "
        f"Add them to the stub — it is the only description of the native surface."
    )
    stale = sorted(declared - actual)
    assert not stale, (
        f"{class_name}: _pyjutsu.pyi declares these, but the extension does not have them: "
        f"{stale}. Remove them from the stub, or rebuild the extension "
        f"(`maturin develop`) if you just added them in Rust."
    )


def test_stub_module_functions_match_the_extension() -> None:
    actual = _public(
        {
            name
            for name, value in vars(ext).items()
            if callable(value) and not isinstance(value, type)
        }
    )
    assert _stub_functions() == actual


def test_every_exception_in_the_stub_exists() -> None:
    """The stub's exception hierarchy must match the one `errors::register` installs."""
    for node in _stub_module().body:
        if not isinstance(node, ast.ClassDef) or not node.bases:
            continue
        cls = getattr(ext, node.name, None)
        assert cls is not None, f"_pyjutsu.pyi declares {node.name}, which the extension lacks"
        assert issubclass(cls, BaseException)
        # The declared base must be the real one, so the stub's `except` hierarchy is honest.
        declared_base = node.bases[0]
        assert isinstance(declared_base, ast.Name)
        expected = declared_base.id
        actual = cls.__bases__[0].__name__
        assert actual == expected, (
            f"{node.name}: stub says it subclasses {expected}, extension says {actual}"
        )
