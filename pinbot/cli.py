import argparse
from . import pins, site


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pinbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="generate static site into docs/")
    sub.add_parser("queue", help="create pins for new products")
    pub = sub.add_parser("publish", help="post pending pins (dry-run unless --live)")
    pub.add_argument("--limit", type=int, default=5)
    pub.add_argument("--live", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        print(f"built {site.build()} products")
    elif a.cmd == "queue":
        print(f"queued {pins.enqueue()} new pins")
    else:
        print(f"processed {pins.publish(a.limit, dry_run=not a.live)} pins")


if __name__ == "__main__":
    main()
