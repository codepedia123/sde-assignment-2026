from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .service import Controller


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Outcome-first autonomous controller")
    parser.add_argument("--url", required=True, help="Simulator base URL")
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("CONTROLLER_STATE_DIR", ".controller-state"),
        help="Durable controller state directory",
    )
    args = parser.parse_args(argv)
    controller = Controller(args.url, Path(args.state_dir))
    try:
        controller.run()
        return 0
    except KeyboardInterrupt:
        print("controller interrupted; durable state is safe", file=sys.stderr)
        return 130
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())

