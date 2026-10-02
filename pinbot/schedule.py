"""Free, API-less route: export a Pinterest bulk-upload CSV with 3 pins/day scheduled."""
import csv
from datetime import datetime, time, timedelta
from .config import ROOT, get
from .pins import load_queue

SLOTS = [time(9, 0), time(14, 0), time(20, 0)]  # 3 pins/day, local time
OUT = ROOT / "data" / "pinterest_bulk.csv"
FIELDS = ["Title", "Description", "Media URL", "Pinterest board", "Thumbnail", "Link", "Publish date", "Keywords"]


def slots(start_day, n):
    day = start_day
    while n > 0:
        for t in SLOTS:
            if n <= 0:
                return
            yield datetime.combine(day, t)
            n -= 1
        day += timedelta(days=1)


def export(per_day_start=None, path=OUT):
    """Write CSV for pending pins. Pinterest bulk upload allows scheduling up to 30 days ahead."""
    pending = [p for p in load_queue() if p["status"] == "pending"]
    start = per_day_start or (datetime.now() + timedelta(days=1)).date()
    board = get("PINTEREST_BOARD_NAME", "Glowing Era")
    rows = list(zip(pending, slots(start, len(pending))))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for p, when in rows:
            w.writerow({
                "Title": p["title"], "Description": p["description"], "Media URL": p["image_url"],
                "Pinterest board": board, "Thumbnail": "", "Link": p["link"],
                "Publish date": when.strftime("%Y-%m-%dT%H:%M:%S"), "Keywords": "",
            })
    return len(rows)
