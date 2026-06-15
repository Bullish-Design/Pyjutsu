# Pyjutsu

A general-purpose, Pythonic + Pydantic binding to **jujutsu's Rust engine (`jj-lib`)** via
PyO3/maturin — native graph, op-log, working-copy, and transaction access **in-process**, with
no subprocess and no text parsing.

- **Import:** `import pyjutsu`
- **Targets:** jujutsu / `jj-lib` **0.38** (version contract: `pyjutsu 0.38.*` ↔ jj 0.38).
- **Spec:** see [`docs/PYJUTSU_CONCEPT.md`](docs/PYJUTSU_CONCEPT.md).

Status: early implementation (M0 — foundation + build spike).

## Development

Everything runs inside the [devenv](https://devenv.sh) shell, which pins the Rust toolchain,
`maturin`, and the matching `jj` 0.38.0 CLI used for differential tests:

```sh
devenv shell -- devenv tasks run pyjutsu:build   # maturin develop
devenv shell -- devenv tasks run pyjutsu:test    # pytest + cargo test
devenv shell -- devenv tasks run pyjutsu:lint    # ruff + clippy
```
