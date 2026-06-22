"""A typed, composable builder that **renders to** jj revset strings (concept §5 "later nicety").

The builder evaluates nothing. It is sugar over the existing string path: every read still takes a
revset *string*, and a :class:`Revset` simply carries the (parenthesis-safe) fragment it renders to.
Compared with hand-built f-strings it removes quoting hazards (values are escaped exactly the way
jj's own ``escape_string`` does) and adds discoverability, while ``raw(...)`` keeps the full revset
language one call away for anything unbound.

Typical use (import the module as ``R``)::

    from pyjutsu import revset as R, Pattern

    ws.log(R.author("alice") & R.description("fix"))      # author + description filter
    ws.log(R.range(R.root(), R.working_copy()))           # root()..@
    ws.log(R.bookmark("main").descendants())              # main::

A bare ``str`` passed to a filter (``author``/``description``/…) is coerced to a *substring* pattern,
matching jj's default in pyjutsu's ``use_glob_by_default=false`` context; pass an explicit
:class:`Pattern` (e.g. ``Pattern.exact("alice")``) for other kinds.
"""

from __future__ import annotations

__all__ = ["Revset", "Pattern"]


def _quote(s: str) -> str:
    """Quote ``s`` as a jj string literal, mirroring jj-lib's ``escape_string`` exactly.

    Verified against ``dsl_util.rs::escape_string`` (jj-lib 0.42.0): ``"`` and ``\\`` are
    backslash-escaped; ``\\t \\r \\n \\0`` use their named forms; any other ASCII control char
    (``< 0x20`` or ``0x7f``) becomes ``\\xNN`` (Rust's ``ascii::escape_default``); everything else —
    printable ASCII and all non-ASCII — passes through verbatim. Rendering then re-parses to the
    identical value jj would have stored.
    """
    out = ['"']
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\t":
            out.append("\\t")
        elif c == "\r":
            out.append("\\r")
        elif c == "\n":
            out.append("\\n")
        elif c == "\0":
            out.append("\\0")
        elif c.isascii() and (ord(c) < 0x20 or ord(c) == 0x7F):
            out.append(f"\\x{ord(c):02x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


class Pattern:
    """A jj *string pattern* ``kind:"value"`` (e.g. ``exact:"main"``, ``glob:"feat/*"``).

    Mirrors jj's string-pattern kinds exactly (verified ``str_util.rs::from_str_kind``): ``exact``,
    ``substring``, ``glob``, ``regex`` plus their case-insensitive ``*_i`` variants (which render
    with jj's hyphenated kind names ``exact-i`` etc.). The value is escaped via :func:`_quote`.
    """

    __slots__ = ("_kind", "_value")

    def __init__(self, kind: str, value: str) -> None:
        self._kind = kind
        self._value = value

    @classmethod
    def exact(cls, value: str) -> Pattern:
        """Match the value exactly (``exact:"..."``)."""
        return cls("exact", value)

    @classmethod
    def exact_i(cls, value: str) -> Pattern:
        """Match the value exactly, case-insensitively (``exact-i:"..."``)."""
        return cls("exact-i", value)

    @classmethod
    def substring(cls, value: str) -> Pattern:
        """Match any value containing this substring (``substring:"..."``). jj's filter default."""
        return cls("substring", value)

    @classmethod
    def substring_i(cls, value: str) -> Pattern:
        """Case-insensitive substring match (``substring-i:"..."``)."""
        return cls("substring-i", value)

    @classmethod
    def glob(cls, value: str) -> Pattern:
        """Match the value against a shell glob (``glob:"..."``)."""
        return cls("glob", value)

    @classmethod
    def glob_i(cls, value: str) -> Pattern:
        """Case-insensitive glob match (``glob-i:"..."``)."""
        return cls("glob-i", value)

    @classmethod
    def regex(cls, value: str) -> Pattern:
        """Match the value against a regular expression (``regex:"..."``)."""
        return cls("regex", value)

    @classmethod
    def regex_i(cls, value: str) -> Pattern:
        """Case-insensitive regex match (``regex-i:"..."``)."""
        return cls("regex-i", value)

    def render(self) -> str:
        """Render to ``kind:"<escaped value>"``."""
        return f"{self._kind}:{_quote(self._value)}"

    def __repr__(self) -> str:
        return f"Pattern({self._kind!r}, {self._value!r})"


def _as_pattern(text: str | Pattern) -> Pattern:
    """Coerce a filter argument: a bare ``str`` becomes a ``substring`` pattern (jj's default)."""
    return text if isinstance(text, Pattern) else Pattern.substring(text)


class Revset:
    """An immutable, already-parenthesis-safe rendered jj revset fragment.

    Build one with the module-level constructors (:func:`all_`, :func:`author`, …) and compose with
    the Python operators below; ``.expr`` (or ``str(...)``) is the rendered string the existing
    evaluator parses. Combinators always wrap each operand in parentheses, so a composed fragment is
    never precedence-ambiguous (over-parenthesizing is harmless).

    ===============  ===============  =====================================
    Python           Renders          jj meaning
    ===============  ===============  =====================================
    ``a & b``        ``(a & b)``      intersection
    ``a | b``        ``(a | b)``      union
    ``a - b``        ``(a ~ b)``      difference
    ``~a``           ``(~a)``         negation (complement)
    ``a.range(b)``   ``(a..b)``       ancestors of ``b`` not of ``a``
    ``a.dag_range(b)`` ``(a::b)``     DAG range
    ``a.ancestors()`` ``(::a)``       ancestors (inclusive)
    ``a.descendants()`` ``(a::)``     descendants (inclusive)
    ===============  ===============  =====================================
    """

    __slots__ = ("_expr",)

    def __init__(self, expr: str) -> None:
        self._expr = expr

    @property
    def expr(self) -> str:
        """The rendered jj revset string."""
        return self._expr

    def __str__(self) -> str:
        return self._expr

    def __repr__(self) -> str:
        return f"Revset({self._expr!r})"

    def __and__(self, other: Revset) -> Revset:
        return Revset(f"({self._expr} & {other._expr})")

    def __or__(self, other: Revset) -> Revset:
        return Revset(f"({self._expr} | {other._expr})")

    def __sub__(self, other: Revset) -> Revset:
        # jj spells set difference as the infix `~`.
        return Revset(f"({self._expr} ~ {other._expr})")

    def __invert__(self) -> Revset:
        return Revset(f"(~{self._expr})")

    def range(self, other: Revset) -> Revset:
        """``self..other`` — ancestors of ``other`` that are not ancestors of ``self``."""
        return Revset(f"({self._expr}..{other._expr})")

    def dag_range(self, other: Revset) -> Revset:
        """``self::other`` — the DAG range between ``self`` and ``other`` (inclusive)."""
        return Revset(f"({self._expr}::{other._expr})")

    def ancestors(self) -> Revset:
        """``::self`` — ``self`` and all its ancestors."""
        return Revset(f"(::{self._expr})")

    def descendants(self) -> Revset:
        """``self::`` — ``self`` and all its descendants."""
        return Revset(f"({self._expr}::)")


# --- Module-level constructors ------------------------------------------------------------------
# Import the module as ``R`` to use these as ``R.author(...)`` etc.


def raw(expr: str) -> Revset:
    """Escape hatch: wrap a caller-supplied literal revset fragment verbatim (no escaping)."""
    return Revset(expr)


def all_() -> Revset:
    """``all()`` — every visible commit."""
    return Revset("all()")


def root() -> Revset:
    """``root()`` — the virtual root commit."""
    return Revset("root()")


def working_copy() -> Revset:
    """``@`` — the working-copy commit."""
    return Revset("@")


def commit(id: str) -> Revset:
    """A commit/change id or symbol, used verbatim (must be non-empty)."""
    if not id:
        raise ValueError("commit() requires a non-empty id or symbol")
    return Revset(id)


def bookmark(name: str) -> Revset:
    """``bookmarks(exact:"<name>")`` — the single bookmark named ``name`` (precise, escaped)."""
    return Revset(f"bookmarks({Pattern.exact(name).render()})")


def author(text: str | Pattern) -> Revset:
    """``author(<pattern>)`` — commits whose author matches (a bare str → substring)."""
    return Revset(f"author({_as_pattern(text).render()})")


def description(text: str | Pattern) -> Revset:
    """``description(<pattern>)`` — commits whose description matches (a bare str → substring)."""
    return Revset(f"description({_as_pattern(text).render()})")


def committer(text: str | Pattern) -> Revset:
    """``committer(<pattern>)`` — commits whose committer matches (a bare str → substring)."""
    return Revset(f"committer({_as_pattern(text).render()})")


def bookmarks(text: str | Pattern | None = None) -> Revset:
    """``bookmarks()`` (all) or ``bookmarks(<pattern>)`` (a bare str → substring)."""
    if text is None:
        return Revset("bookmarks()")
    return Revset(f"bookmarks({_as_pattern(text).render()})")


def tags() -> Revset:
    """``tags()`` — commits pointed at by a tag."""
    return Revset("tags()")


def heads(x: Revset) -> Revset:
    """``heads(<x>)`` — the heads of set ``x`` (no descendants within ``x``)."""
    return Revset(f"heads({x._expr})")


def roots(x: Revset) -> Revset:
    """``roots(<x>)`` — the roots of set ``x`` (no ancestors within ``x``)."""
    return Revset(f"roots({x._expr})")


def parents(x: Revset) -> Revset:
    """``parents(<x>)`` — the parents of every commit in ``x``."""
    return Revset(f"parents({x._expr})")


def children(x: Revset) -> Revset:
    """``children(<x>)`` — the children of every commit in ``x``."""
    return Revset(f"children({x._expr})")


def latest(x: Revset, count: int | None = None) -> Revset:
    """``latest(<x>)`` or ``latest(<x>, <count>)`` — the newest commit(s) of ``x`` by committer date."""
    if count is None:
        return Revset(f"latest({x._expr})")
    return Revset(f"latest({x._expr}, {count})")


def range(a: Revset, b: Revset) -> Revset:
    """``a..b`` — ancestors of ``b`` that are not ancestors of ``a`` (functional form of ``a.range(b)``)."""
    return a.range(b)


def dag_range(a: Revset, b: Revset) -> Revset:
    """``a::b`` — the DAG range between ``a`` and ``b`` (functional form of ``a.dag_range(b)``)."""
    return a.dag_range(b)


def ancestors(x: Revset) -> Revset:
    """``::x`` — ``x`` and all its ancestors (functional form of ``x.ancestors()``)."""
    return x.ancestors()


def descendants(x: Revset) -> Revset:
    """``x::`` — ``x`` and all its descendants (functional form of ``x.descendants()``)."""
    return x.descendants()


def _revset_str(revset: str | Revset) -> str:
    """Coerce a read argument to the revset string the native layer expects."""
    return revset.expr if isinstance(revset, Revset) else revset
