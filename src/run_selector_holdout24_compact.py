"""Compact console output for the frozen holdout runner.

Only presentation changes: all computation, validation, and persistence use
the unmodified frozen functions. In particular, do not print matrix records
containing the full collection of error samples to the terminal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_selector_holdout24 import direct_cost, matrix, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("matrix", "cost", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.phase == "verify":
        result = verify(root)
    else:
        runner = matrix if args.phase == "matrix" else direct_cost
        result = runner(root, resume=args.resume)
    keys = ("record_count", "rows", "passed", "error_rows", "physical_traces",
            "event_geometry_records", "direct_timing_rows", "raw_files_checked",
            "full_cost")
    print(json.dumps({key: result[key] for key in keys if key in result},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
