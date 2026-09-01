#!/usr/bin/env python3
"""Read-only bridge: wave-based pipeline runs → agent-roster ingest.jsonl.

Example of a non-invasive external adapter for the agent-roster plugin: it
never touches the pipeline itself — it watches run directories and emits or
updates session records in ``~/.hermes/plugins/agent-roster/ingest.jsonl``.
Records keep stable ids, so re-emitting a record updates it in place (last
line wins): a run shows up as *running* and later closes with its duration.

Expected run layout (adapt `records_for_run` to your own pipeline):
    <daily-dir>/<YYYY-MM-DD>-<CODE>[-rN]/
        run.log                  # pipeline log (a traceback marks a crash)
        summary.md               # written when the run completes
        wave-*/                  # one directory per generation wave
            *.png                # rendered images

Usage (cron every minute, a systemd timer, or --loop N):
    python3 pipeline_bridge.py --daily-dir ~/pipelines/daily \\
        --run-agent pipeline --render-agent renderer \\
        --group-profiles bot-writer,bot-judge --loop 30

`--group-profiles` lists the Hermes profiles your pipeline invokes
(`hermes -p <profile> -z ...`): their native sessions then nest under the
run in the plugin timeline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

INGEST = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "plugins" / "agent-roster" / "ingest.jsonl"
RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[A-Za-z]\d+(?:-r\d+)?$")
FRESH_DAYS = 3  # only look at recent run dirs

ARGS = None  # set in main()


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _existing_records() -> dict:
    """id -> record as currently ingested (last line wins)."""
    out = {}
    if INGEST.exists():
        for line in INGEST.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and rec.get("id"):
                    out[str(rec["id"])] = rec
            except Exception:
                continue
    return out


def records_for_run(run: Path) -> list:
    """Build the current set of records describing one run directory."""
    rid = run.name
    recs = []
    started = _mtime(run)
    done = (run / "summary.md").exists()
    log = run / "run.log"
    crashed = False
    if log.exists():
        started = min(filter(None, (started, _mtime(log)))) or started
        tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
        crashed = "Traceback (most recent call last)" in tail and not done
    root = {
        "id": rid,
        "agent": ARGS.run_agent,
        "title": f"Run {rid}",
        "started_at": started,
        "model": "pipeline",
        "group_profiles": ARGS.group_profiles,
    }
    if done:
        root["ended_at"] = _mtime(run / "summary.md")
    elif crashed:
        root["ended_at"] = _mtime(log)
        root["error"] = True
    recs.append(root)

    # The agents driven through Hermes profiles need no records here: the
    # plugin's profile scan picks their real sessions up natively, and
    # `group_profiles` above nests them under this run. The bridge only
    # covers what has no Hermes footprint — e.g. the image renderer.
    for wave in sorted(run.glob("wave-*")):
        images = sorted(wave.glob("*.png"))
        if images:
            def _display(p: Path) -> str:
                ev = p.parent / (p.stem + ".eval.jpg")
                return str(ev) if ev.exists() else str(p)

            recs.append({
                "id": f"{rid}-{wave.name}-render",
                "agent": ARGS.render_agent,
                "title": f"Rendered {wave.name} — {len(images)} image{'s' if len(images) > 1 else ''}",
                "started_at": _mtime(images[0]),
                "ended_at": _mtime(images[-1]),
                "parent_id": rid,
                "tool_calls": len(images),
                "images": [_display(p) for p in images],
            })
    return recs


def sync(daily: Path) -> int:
    cutoff = time.time() - FRESH_DAYS * 86400
    existing = _existing_records()
    new_lines = []
    for run in sorted(daily.iterdir()):
        if not run.is_dir() or not RUN_DIR_RE.match(run.name) or _mtime(run) < cutoff:
            continue
        for rec in records_for_run(run):
            if existing.get(str(rec["id"])) != rec:
                new_lines.append(json.dumps(rec, ensure_ascii=False))
    if new_lines:
        INGEST.parent.mkdir(parents=True, exist_ok=True)
        with INGEST.open("a", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")
    return len(new_lines)


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daily-dir", required=True, help="directory holding the pipeline run directories")
    ap.add_argument("--run-agent", default="pipeline", help="agent name for the run itself")
    ap.add_argument("--render-agent", default="renderer", help="agent name for the image renderer")
    ap.add_argument("--group-profiles", default="", help="comma-separated Hermes profiles the pipeline invokes")
    ap.add_argument("--loop", type=int, default=0, help="poll every N seconds (0 = one shot)")
    args = ap.parse_args()
    args.group_profiles = [p.strip() for p in args.group_profiles.split(",") if p.strip()]
    ARGS = args
    daily = Path(args.daily_dir).expanduser()
    while True:
        n = sync(daily)
        if n:
            print(f"[pipeline-bridge] {n} record(s) emitted")
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
