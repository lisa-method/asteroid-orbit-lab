"""Check the portable documentation and reviewed media without raw ephemerides."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENTRY_DOCS = (
    "README.md", "docs/START_HERE.md", "docs/PHYSICS_STORIES.md",
    "docs/TRAJECTORY_SHOWCASE.md", "docs/REPRODUCIBILITY.md", "examples/README.md",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    problems = []
    links = 0
    for name in ENTRY_DOCS:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            target = target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            linked = path.parent / unquote(parsed.path)
            links += 1
            if not linked.exists():
                problems.append(f"Broken local link: {name} -> {target}")
        if re.search(r"(?:/Users/|/home/)[^\s]+", text):
            problems.append(f"Local home path in public entry document: {name}")
    gallery = ROOT / "docs/figures/trajectory_showcase"
    manifest = json.loads((gallery / "manifest.json").read_text())
    checked = set()
    for entry in manifest["entries"]:
        for name, expected in entry["files"].items():
            path = gallery / name
            if not path.is_file():
                problems.append(f"Missing media: {name}")
                continue
            if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
                problems.append(f"Changed reviewed media: {name}")
            checked.add(name)
    gifs = {name for name in checked if name.endswith(".gif")}
    if len(gifs) != 19:
        problems.append(f"Expected 19 reviewed GIFs, found {len(gifs)}")
    gallery_text = (ROOT / "docs/TRAJECTORY_SHOWCASE.md").read_text()
    for name in gifs:
        if f"figures/trajectory_showcase/{name}" not in gallery_text:
            problems.append(f"GIF absent from gallery navigation: {name}")
    if problems:
        raise SystemExit("\n".join(problems))
    print(f"Passed: {links} entry-document links; {len(checked)} media files; all 19 GIFs linked.")
    print("Integrity check only: this does not rerun the original coordinate or scientific audits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
