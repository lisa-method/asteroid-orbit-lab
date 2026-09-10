"""Console-only wrapper around the frozen v4 experiment functions."""
import argparse
import json
from pathlib import Path

from run_selector_v4_holdout24 import matrix, direct_cost, verify


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("matrix", "cost", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    fn = {"matrix": matrix, "cost": direct_cost, "verify": verify}[args.phase]
    result = fn(args.root.resolve()) if args.phase == "verify" else fn(args.root.resolve(), resume=args.resume)
    print(json.dumps({k: result[k] for k in ("record_count", "rows", "passed", "error_rows", "physical_traces", "full_cost") if k in result}, indent=2))
