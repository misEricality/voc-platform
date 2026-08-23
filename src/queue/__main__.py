"""Bili collection queue CLI entrypoint.

Usage:
    python -m src.queue add BV1xxx BV2xxx
    python -m src.queue list [--status pending]
    python -m src.queue due
    python -m src.queue run-due [--limit 50] [--dry-run]
    python -m src.queue skip BV1xxx --reason "non-review"
    python -m src.queue remove BV1xxx
    python -m src.queue show BV1xxx
"""
from src.queue.cli import main

raise SystemExit(main())