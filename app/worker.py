"""Scheduler worker. Publishes DUE scheduled pins through the same publish_pin() the Publish button uses.

  python -m app.worker --once        run one pass now
  python -m app.worker               loop every 60s (requires SCHEDULER_ENABLED=true: automatic publishing is opt-in)
"""
from __future__ import annotations

import argparse
import logging
import time

from .config import get_settings
from .main import create_app
from .services import publishing

log = logging.getLogger("engine.worker")


def run_once(app) -> dict:
    with app.state.db.session() as db:
        return publishing.process_due(db, app.state.settings, app.state.pinterest)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.worker")
    ap.add_argument("--once", action="store_true", help="run a single pass and exit")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args(argv)
    settings = get_settings()
    app = create_app(settings)
    if args.once:
        print(run_once(app))
        return 0
    if not settings.scheduler_enabled:
        print("Automatic publishing is OFF. Set SCHEDULER_ENABLED=true to let the worker publish scheduled pins.")
        return 1
    log.info("worker started (interval %ss)", args.interval)
    while True:
        try:
            report = run_once(app)
            if report["published"] or report["failed"]:
                log.info("worker pass: %s", {k: v for k, v in report.items() if k != "messages"})
        except Exception:
            log.exception("worker pass failed")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
