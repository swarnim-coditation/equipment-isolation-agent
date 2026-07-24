"""Regenerate the drawing list inside output/index.html.

A file:// page cannot list its own directory, so index.html carries a baked-in
DRAWINGS array. That array goes stale whenever a drawing is added or deleted --
dead links for removed tags, missing buttons for new ones. This rewrites the
static index from the built-in template and the files that are actually on disk.

    uv run python scripts/build_index.py            # rewrite output/index.html
    uv run python scripts/build_index.py --check    # report drift, change nothing

The P&ID name for each tag is read from its own payload (data[0].job_name), so
the grouping stays correct without a hardcoded mapping.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output"
ARRAY_RE = re.compile(r"const DRAWINGS = \[.*?\];", re.S)
DEFAULT_INDEX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Equipment Isolation — Generated Drawings</title>
<style>
body { font-family: Arial, sans-serif; margin: 24px; color: #111827; background: #f9fafb; }
h1 { margin: 0 0 4px; font-size: 22px; }
.sub { margin: 0 0 18px; color: #4b5563; font-size: 13px; }
.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
#filter {
  flex: 1; min-width: 240px; max-width: 420px; padding: 9px 12px; font-size: 14px;
  border: 1px solid #d1d5db; border-radius: 6px; background: #fff; color: #111827;
}
#filter:focus { outline: none; border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37,99,235,0.12); }
.count { color: #6b7280; font-size: 13px; }
.group { margin: 0 0 22px; }
.group-title {
  font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
  color: #6b7280; margin: 0 0 10px; padding-bottom: 6px; border-bottom: 1px solid #e5e7eb;
}
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.card {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  text-decoration: none; color: inherit; background: #fff;
  border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04); transition: border-color .12s, box-shadow .12s, transform .12s;
}
.card:hover { border-color: #2563eb; box-shadow: 0 4px 10px rgba(0,0,0,0.08); transform: translateY(-1px); }
.card:hover .open { color: #2563eb; }
.tag { font-size: 15px; font-weight: 700; }
.open { font-size: 12px; color: #9ca3af; font-weight: 600; white-space: nowrap; }
.empty { color: #6b7280; font-size: 14px; padding: 20px 0; }
.foot { margin-top: 24px; padding-top: 12px; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 11px; }
</style>
</head>
<body>
<h1>Equipment Isolation — Generated Drawings</h1>
<p class="sub">Static index of the drawings in <code>output/</code>. Click a tag to open its overlay in a new tab.</p>

<div class="toolbar">
  <input id="filter" type="text" placeholder="Filter by equipment tag or P&amp;ID..." autocomplete="off" />
  <span class="count" id="count"></span>
</div>

<div id="groups"></div>

<div class="foot">
  Navigation only — open a drawing for its isolation status and any data warnings.
  This list is baked in (a local file cannot read its own directory); regenerate it after running new equipment.
</div>

<script>
// Baked in at creation time: a file:// page cannot list its own directory.
// Deliberately no status/counts here — a static index would go stale and mislead.
// The drawing itself is the source of truth.
const DRAWINGS = [];

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const card = (d) => `
  <a class="card" href="${esc(d.tag)}.html" target="_blank" rel="noopener">
    <span class="tag">${esc(d.tag)}</span>
    <span class="open">Open ↗</span>
  </a>`;

function render(q = "") {
  const needle = q.trim().toLowerCase();
  const hits = DRAWINGS.filter(
    (d) => !needle || d.tag.toLowerCase().includes(needle) || d.pid.toLowerCase().includes(needle)
  );
  const byPid = {};
  hits.forEach((d) => (byPid[d.pid] = byPid[d.pid] || []).push(d));

  document.getElementById("count").textContent =
    `${hits.length} of ${DRAWINGS.length} drawing${DRAWINGS.length === 1 ? "" : "s"}`;

  document.getElementById("groups").innerHTML = hits.length
    ? Object.keys(byPid).map((pid) => `
        <div class="group">
          <div class="group-title">${esc(pid)}</div>
          <div class="grid">${byPid[pid].map(card).join("")}</div>
        </div>`).join("")
    : '<div class="empty">No drawings match that filter.</div>';
}

document.getElementById("filter").addEventListener("input", (e) => render(e.target.value));
render();
</script>
</body>
</html>
"""


def discover(output_dir: Path) -> list[dict]:
    """Every tag with BOTH a .html and a .json, sorted by P&ID then tag."""
    rows = []
    for html_path in sorted(output_dir.glob("*.html")):
        tag = html_path.stem
        if tag == "index":
            continue
        json_path = output_dir / f"{tag}.json"
        if not json_path.exists():
            print(f"  skip {tag}: no {json_path.name} alongside it", file=sys.stderr)
            continue
        try:
            record = (json.loads(json_path.read_text(encoding="utf-8")).get("data") or [{}])[0]
            pid = str(record.get("job_name") or "").strip() or "Unknown P&ID"
        except Exception as exc:
            print(f"  skip {tag}: unreadable payload ({exc})", file=sys.stderr)
            continue
        rows.append({"tag": tag, "pid": pid})
    return sorted(rows, key=lambda r: (r["pid"], r["tag"]))


def render_array(rows: list[dict]) -> str:
    if not rows:
        return "const DRAWINGS = [];"
    width = max(len(r["tag"]) for r in rows) + 2
    lines = [f'  {{ tag: {chr(34)+r["tag"]+chr(34):<{width}}, pid: "{r["pid"]}" }},' for r in rows]
    return "const DRAWINGS = [\n" + "\n".join(lines) + "\n];"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="Report drift without writing.")
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    index_path = output_dir / "index.html"

    rows = discover(output_dir)
    existing_source = index_path.read_text(encoding="utf-8") if index_path.exists() else DEFAULT_INDEX_HTML
    source = existing_source if args.check else DEFAULT_INDEX_HTML
    if not ARRAY_RE.search(source):
        print("ERROR: could not find the DRAWINGS array in index.html", file=sys.stderr)
        return 2

    listed = set(re.findall(r'tag:\s*"([^"]+)"', ARRAY_RE.search(existing_source).group(0)))
    found = {r["tag"] for r in rows}
    dead, missing = sorted(listed - found), sorted(found - listed)
    if args.check or index_path.exists():
        for tag in dead:
            print(f"  dead link:      {tag} (listed, no file)")
        for tag in missing:
            print(f"  missing button: {tag} (file present, not listed)")

    if args.check:
        if dead or missing:
            print(f"\nindex.html is out of date ({len(dead)} dead, {len(missing)} missing).")
            return 1
        print(f"index.html is up to date ({len(rows)} drawings).")
        return 0

    index_path.write_text(ARRAY_RE.sub(lambda _: render_array(rows), source, count=1), encoding="utf-8")
    print(f"\nWrote {len(rows)} drawing(s) to {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
