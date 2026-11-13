"""Command-line entry point: store + describe a local file without the web UI.

    uv run python cli.py path/to/file.jpg [more files...]
    uv run python cli.py --check        # verify B2 + Claude credentials
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path

from app.config import get_settings
from app.describer import Describer
from app.service import FileService
from app.storage import B2Storage, StorageError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--check", action="store_true", help="verify credentials and exit")
    ap.add_argument("--json", action="store_true", help="print raw JSON results")
    args = ap.parse_args()

    s = get_settings()
    describer = Describer(s)
    if missing := s.b2_missing():
        print(f"✗ B2 not configured — missing {', '.join(missing)} in .env (see docs/B2_SETUP.md)")
        return 2
    storage = B2Storage(s)

    if args.check:
        ok = True
        try:
            storage.check()
            print(f"✓ B2 bucket '{s.b2_bucket}' reachable at {s.b2_endpoint_url}")
        except StorageError as e:
            ok = False
            print(f"✗ {e}")
        print(("✓" if describer.configured else "✗") + f" Claude key {'found' if describer.configured else 'missing'} (model {s.claude_model})")
        return 0 if ok and describer.configured else 1

    if not args.paths:
        ap.print_help()
        return 2

    service = FileService(storage, describer)
    code = 0
    for p in args.paths:
        if not p.is_file():
            print(f"✗ {p}: not a file")
            code = 1
            continue
        r = service.process(p.read_bytes(), p.name, mimetypes.guess_type(p.name)[0], source="cli")
        if args.json:
            print(json.dumps(r.to_dict(), indent=2))
        else:
            icon = {"described": "✓", "stored": "◐", "failed": "✗"}[r.status]
            print(f"{icon} {r.filename} ({r.size:,} B) → {r.status}  [{r.key}]")
            if r.description:
                print(f"   {r.description_kind}: {r.description}")
                if r.tags:
                    print(f"   tags: {', '.join(r.tags)}")
            if r.error:
                print(f"   note: {r.error}")
            print(f"   {r.timings_ms}")
        code |= r.status == "failed"
    return code


if __name__ == "__main__":
    sys.exit(main())
