"""``python -m pyjutsu`` runs the same command line as the ``pyjutsu`` executable."""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
