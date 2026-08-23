# -*- coding: utf-8 -*-
"""Bili Collection Queue Module.

Design:
- Engineers manually add BV IDs to the "to-collect list" (status=pending).
- After recognizing pubdate, compute due_date (pubdate + 7d), set status=scheduled.
- Daily cron scans status=scheduled AND due_date <= today, calls pipeline.
- Success marks fetched; failure retries N times then dead-letters.

CLI (python -m src.queue <subcmd>):
- add BV [BV ...]    add BV IDs (auto-recognize pubdate)
- list [--status X]  list entries
- due [--limit N]    list today's due tasks
- run-due [--limit N]  trigger today's collection
- skip BV --reason X  skip a BV (mark failed)
- remove BV          remove entry (only when pending/scheduled/failed)
- show BV            show details

Future extensions (v2):
- Auto-import candidates via keywords (status=candidate)
- Frontend visualization (admin UI reads bilibili_queue table)
"""