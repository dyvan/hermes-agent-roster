"""Agent Roster dashboard plugin backend.

Derives "agents" from real session data (grouped by system prompt hash),
serves per-agent aggregates (runs / active time / cost per period) and a
chronological session timeline. User-defined identity overrides (name,
tagline, skills, color) are stored under
``~/.hermes/plugins/agent-roster/identities.json``.

Mounted at /api/plugins/agent-roster/ by the Hermes dashboard.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from hermes_constants import get_hermes_home
except ImportError:  # pragma: no cover - test fallback
    import os as _os

    def get_hermes_home() -> Path:  # type: ignore[misc]
        val = (_os.environ.get("HERMES_HOME") or "").strip()
        return Path(val) if val else Path.home() / ".hermes"

try:
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse
except Exception:  # pragma: no cover - allows unit tests without fastapi
    FileResponse = None  # type: ignore
    class HTTPException(Exception):  # type: ignore
        def __init__(self, status_code: int, detail: str = ""):
            self.status_code = status_code
            self.detail = detail

    class APIRouter:  # type: ignore
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

        def post(self, *_args, **_kwargs):
            return lambda fn: fn

router = APIRouter()

AGENT_KEY_LEN = 12
_IDENTITY_LOCK = threading.Lock()

# Deterministic avatar palette — index picked from the agent key hash.
_PALETTE = [
    ("#5B7CFA", "#3B4FD8"),
    ("#E8734A", "#C2451F"),
    ("#2FB98B", "#0E8A62"),
    ("#B96BD6", "#8A3FB0"),
    ("#E0A458", "#B07A2E"),
    ("#4FB3D9", "#2A7FA6"),
    ("#D96B8A", "#B03A5E"),
    ("#8AC24A", "#5A8F1F"),
]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _identities_path() -> Path:
    d = get_hermes_home() / "plugins" / "agent-roster"
    d.mkdir(parents=True, exist_ok=True)
    return d / "identities.json"


def _load_identities() -> Dict[str, Any]:
    try:
        data = json.loads(_identities_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_identities(data: Dict[str, Any]) -> None:
    path = _identities_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Session scan
# ---------------------------------------------------------------------------

def _open_db():
    from hermes_state import SessionDB

    return SessionDB()


def agent_key_for(system_prompt_hash: Optional[str], source: Optional[str], model: Optional[str]) -> str:
    """Stable agent key: the system prompt identity when known, else source+model."""
    if system_prompt_hash:
        return system_prompt_hash[:AGENT_KEY_LEN]
    basis = f"{source or 'unknown'}|{model or ''}"
    return "src-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8]


def default_name(source: Optional[str], model: Optional[str], key: str) -> str:
    src = (source or "unknown").upper()
    if model:
        short = model.split("/")[-1].split(":")[0]
        return f"{src} · {short}"
    return f"{src} · {key[:4]}"


def _avatar_for(key: str) -> Dict[str, str]:
    idx = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % len(_PALETTE)
    a, b = _PALETTE[idx]
    return {"from": a, "to": b}


_SESSIONS_SQL = """
    SELECT s.id, s.source, s.model, s.title, s.started_at, s.ended_at,
           s.end_reason, s.message_count, s.tool_call_count,
           s.input_tokens, s.output_tokens,
           COALESCE(s.actual_cost_usd, s.estimated_cost_usd, 0) AS cost_usd,
           s.parent_session_id, s.system_prompt_hash, s.display_name,
           (SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = s.id)
               AS last_msg_at
    FROM sessions s
    WHERE s.archived = 0 AND s.started_at > ?
    ORDER BY s.started_at DESC
"""


def _fetch_sessions(days: int) -> List[Dict[str, Any]]:
    """Read sessions (+ last message timestamp) from the Hermes state DB."""
    db = _open_db()
    try:
        cutoff = time.time() - days * 86400
        cur = db._conn.execute(_SESSIONS_SQL, (cutoff,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Hermes profiles — each ``~/.hermes/profiles/<name>/`` is a full Hermes home
# with its own state.db. A profile is the most natural "agent": aggregate its
# sessions under one identity, read-only.
# ---------------------------------------------------------------------------

def _profile_dirs() -> List[Any]:
    root = get_hermes_home() / "profiles"
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "state.db").exists():
            out.append(d)
    return out


def profile_key(name: str) -> str:
    return "prof-" + name


def _open_profile_db(name: str) -> Optional[sqlite3.Connection]:
    path = get_hermes_home() / "profiles" / name / "state.db"
    if not path.exists() or "/" in name or "\\" in name or ".." in name:
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_profile_sessions(days: int) -> List[Dict[str, Any]]:
    cutoff = time.time() - days * 86400
    rows: List[Dict[str, Any]] = []
    for d in _profile_dirs():
        name = d.name
        try:
            conn = _open_profile_db(name)
            if conn is None:
                continue
            try:
                for r in conn.execute(_SESSIONS_SQL, (cutoff,)).fetchall():
                    row = dict(r)
                    row["id"] = f"prof:{name}:{row['id']}"
                    # Parent links point inside the same profile DB.
                    if row.get("parent_session_id"):
                        row["parent_session_id"] = f"prof:{name}:{row['parent_session_id']}"
                    row["_agent_key"] = profile_key(name)
                    row["display_name"] = name
                    row["_profile"] = name
                    rows.append(row)
            finally:
                conn.close()
        except Exception:
            continue  # older schema or locked profile — skip, never break the page
    return rows


def _profile_soul_tagline(name: str) -> str:
    """First descriptive line of the profile's SOUL.md, as a default tagline."""
    soul = get_hermes_home() / "profiles" / name / "SOUL.md"
    try:
        for line in soul.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:140]
    except Exception:
        pass
    return ""


def _profile_prompt(name: str) -> Optional[str]:
    conn = _open_profile_db(name)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT system_prompt FROM sessions WHERE system_prompt IS NOT NULL"
            " ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    finally:
        conn.close()
    soul = get_hermes_home() / "profiles" / name / "SOUL.md"
    try:
        return soul.read_text(encoding="utf-8")
    except Exception:
        return None


def _latest_prompt_for(key: str) -> Optional[str]:
    """Full system prompt of the most recent session belonging to this agent."""
    db = _open_db()
    try:
        cur = db._conn.execute(
            """
            SELECT system_prompt FROM sessions
            WHERE archived = 0 AND system_prompt IS NOT NULL
              AND substr(system_prompt_hash, 1, ?) = ?
            ORDER BY started_at DESC LIMIT 1
            """,
            (AGENT_KEY_LEN, key),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# External ingestion — sessions produced outside the Hermes session engine
# (no-agent cron scripts, CI pipelines, standalone bots). Producers append
# one JSON object per line to ``~/.hermes/plugins/agent-roster/ingest.jsonl``
# (no auth needed, local FS) or POST to /ingest (dashboard token required).
# Schema per record: {agent, title, started_at, ended_at?|duration_seconds?,
#   model?, cost_usd?, input_tokens?, output_tokens?, tool_calls?, parent_id?,
#   error?, id?, messages?: [{role, content}]}
# ---------------------------------------------------------------------------

_INGEST_LOCK = threading.Lock()


def _ingest_path() -> Path:
    d = get_hermes_home() / "plugins" / "agent-roster"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ingest.jsonl"


def external_key(agent_name: str) -> str:
    return "ext-" + hashlib.sha256(agent_name.strip().lower().encode("utf-8")).hexdigest()[:8]


def normalize_external(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn one ingested record into the internal session-row shape."""
    try:
        started = float(rec.get("started_at"))
    except (TypeError, ValueError):
        return None
    agent = str(rec.get("agent") or "external").strip() or "external"
    key = external_key(agent)
    ended = rec.get("ended_at")
    if ended is None and rec.get("duration_seconds") is not None:
        try:
            ended = started + float(rec["duration_seconds"])
        except (TypeError, ValueError):
            ended = None
    rid = str(rec.get("id") or f"{int(started)}-{key}")

    def _num(field, cast):
        try:
            return cast(rec.get(field) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "id": "ext:" + rid,
        "source": "external",
        "model": rec.get("model"),
        "title": str(rec.get("title") or agent),
        "started_at": started,
        "ended_at": float(ended) if ended is not None else None,
        "end_reason": "error" if rec.get("error") else None,
        "message_count": len(rec.get("messages") or []),
        "tool_call_count": _num("tool_calls", int),
        "input_tokens": _num("input_tokens", int),
        "output_tokens": _num("output_tokens", int),
        "cost_usd": _num("cost_usd", float),
        "parent_session_id": ("ext:" + str(rec["parent_id"])) if rec.get("parent_id") else None,
        "system_prompt_hash": None,
        "display_name": agent,
        "last_msg_at": None,
        "_agent_key": key,
        # Optional grouping: profile sessions started inside this record's
        # time window and whose profile is listed here become its children.
        "_group_profiles": [str(p) for p in (rec.get("group_profiles") or []) if str(p).strip()],
        # Optional gallery: absolute paths of images produced by this session.
        # Served only through /external/{id}/image/{n} after re-validation.
        "_images": [str(p) for p in (rec.get("images") or []) if isinstance(p, str)],
    }


def _load_external(days: int) -> List[Dict[str, Any]]:
    path = _ingest_path()
    if not path.exists():
        return []
    cutoff = time.time() - days * 86400
    by_id: Dict[str, Dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        row = normalize_external(rec) if isinstance(rec, dict) else None
        if row and row["started_at"] > cutoff:
            by_id[row["id"]] = row  # last line with a given id wins (live updates)
    rows = sorted(by_id.values(), key=lambda r: r["started_at"], reverse=True)
    return rows


def _external_record(rec_id: str) -> Optional[Dict[str, Any]]:
    """Latest normalized record with this id from ingest.jsonl, or None."""
    path = _ingest_path()
    if not path.exists():
        return None
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            row = normalize_external(rec)
            if row and row["id"] == "ext:" + rec_id:
                found = (row, rec)
    return found[1] if found else None


def _external_messages(rec_id: str) -> Optional[List[Dict[str, Any]]]:
    path = _ingest_path()
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        row = normalize_external(rec)
        if row and row["id"] == "ext:" + rec_id:
            msgs = rec.get("messages") or []
            return [
                {"role": str(m.get("role") or "assistant"), "content": str(m.get("content") or "")}
                for m in msgs if isinstance(m, dict)
            ]
    return None


def _duration_seconds(row: Dict[str, Any]) -> float:
    end = row.get("ended_at") or row.get("last_msg_at") or row.get("started_at")
    try:
        return max(0.0, float(end) - float(row["started_at"]))
    except (TypeError, ValueError, KeyError):
        return 0.0


RUNNING_GRACE_SECONDS = 300  # same window the core dashboard uses for "active"


def _is_running(row: Dict[str, Any], now: Optional[float] = None) -> bool:
    """A session with no recorded end is running; native sessions must also
    show recent message activity (the core never backfills ended_at on crash)."""
    if row.get("ended_at") is not None:
        return False
    if row.get("source") == "external":
        return True
    now = now or time.time()
    last = row.get("last_msg_at") or row.get("started_at") or 0
    return (now - float(last)) < RUNNING_GRACE_SECONDS


def _is_error(row: Dict[str, Any]) -> bool:
    reason = (row.get("end_reason") or "").lower()
    return any(w in reason for w in ("error", "failed", "failure", "crash"))


def build_roster(rows: List[Dict[str, Any]], identities: Dict[str, Any], now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Aggregate sessions into agents with day/week/month stats."""
    now = now or time.time()
    lt = time.localtime(now)
    midnight = now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
    week_cutoff = now - 7 * 86400
    month_cutoff = now - 30 * 86400

    agents: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = row.get("_agent_key") or agent_key_for(row.get("system_prompt_hash"), row.get("source"), row.get("model"))
        a = agents.get(key)
        if a is None:
            a = agents[key] = {
                "key": key,
                "sources": {},
                "models": {},
                "first_seen": row["started_at"],
                "last_seen": row["started_at"],
                "display_names": {},
                "stats": {
                    p: {"runs": 0, "active_seconds": 0.0, "cost_usd": 0.0,
                        "input_tokens": 0, "output_tokens": 0, "errors": 0}
                    for p in ("day", "week", "month")
                },
            }
        src = row.get("source") or "unknown"
        a["sources"][src] = a["sources"].get(src, 0) + 1
        if row.get("model"):
            a["models"][row["model"]] = a["models"].get(row["model"], 0) + 1
        if row.get("display_name"):
            a["display_names"][row["display_name"]] = a["display_names"].get(row["display_name"], 0) + 1
        a["first_seen"] = min(a["first_seen"], row["started_at"])
        a["last_seen"] = max(a["last_seen"], row["started_at"])

        started = row["started_at"]
        periods = ["month"] if started > month_cutoff else []
        if started > week_cutoff:
            periods.append("week")
        if started > midnight:
            periods.append("day")
        dur = _duration_seconds(row)
        err = _is_error(row)
        for p in periods:
            st = a["stats"][p]
            st["runs"] += 1
            st["active_seconds"] += dur
            st["cost_usd"] += row.get("cost_usd") or 0.0
            st["input_tokens"] += row.get("input_tokens") or 0
            st["output_tokens"] += row.get("output_tokens") or 0
            if err:
                st["errors"] += 1

    out: List[Dict[str, Any]] = []
    for key, a in agents.items():
        ident = identities.get(key) or {}
        models = sorted(a["models"], key=a["models"].get, reverse=True)
        top_source = max(a["sources"], key=a["sources"].get) if a["sources"] else None
        observed_name = None
        if a["display_names"]:
            observed_name = max(a["display_names"], key=a["display_names"].get)
        out.append({
            "key": key,
            "name": ident.get("name") or observed_name
                    or default_name(top_source, models[0] if models else None, key),
            "tagline": ident.get("tagline") or "",
            "skills": ident.get("skills") or [],
            "avatar": {**_avatar_for(key), **({"from": ident["color"], "to": ident["color"]} if ident.get("color") else {})},
            "customized": key in identities,
            "sources": a["sources"],
            "models": models,
            "first_seen": a["first_seen"],
            "last_seen": a["last_seen"],
            "stats": a["stats"],
        })
    out.sort(key=lambda x: x["stats"]["month"]["runs"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/roster")
async def get_roster(days: int = 30):
    days = max(days, 30)
    rows = _fetch_sessions(days) + _fetch_profile_sessions(days) + _load_external(days)
    agents = build_roster(rows, _load_identities())
    for a in agents:
        if a["key"].startswith("prof-") and not a["tagline"]:
            a["tagline"] = _profile_soul_tagline(a["key"][5:])
    return {"agents": agents, "generated_at": time.time()}


def attach_profile_sessions_to_groups(rows: List[Dict[str, Any]], now: Optional[float] = None) -> None:
    """Nest profile sessions under external records that declared them.

    An external record carrying ``group_profiles`` adopts every session of a
    listed profile that started inside its [start, end] window (end = now
    while running). Safe as long as such runs never overlap — the adopter is
    the latest matching window.
    """
    now = now or time.time()
    groups = [r for r in rows if r.get("_group_profiles")]
    if not groups:
        return
    for row in rows:
        prof = row.get("_profile")
        if not prof or row.get("parent_session_id"):
            continue
        best = None
        for g in groups:
            if prof not in g["_group_profiles"]:
                continue
            end = g.get("ended_at") or now
            if g["started_at"] <= row["started_at"] <= end:
                if best is None or g["started_at"] > best["started_at"]:
                    best = g
        if best is not None:
            row["parent_session_id"] = best["id"]


@router.get("/timeline")
async def get_timeline(days: int = 7, agent: str = "", limit: int = 200):
    rows = _fetch_sessions(days) + _fetch_profile_sessions(days) + _load_external(days)
    attach_profile_sessions_to_groups(rows)
    rows.sort(key=lambda r: r["started_at"], reverse=True)
    sessions = []
    for row in rows:
        key = row.get("_agent_key") or agent_key_for(row.get("system_prompt_hash"), row.get("source"), row.get("model"))
        if agent and key != agent:
            continue
        sessions.append({
            "id": row["id"],
            "agent_key": key,
            "title": row.get("title") or row.get("display_name") or "(untitled session)",
            "source": row.get("source"),
            "model": row.get("model"),
            "started_at": row["started_at"],
            "duration_seconds": (time.time() - row["started_at"]) if _is_running(row) else _duration_seconds(row),
            "cost_usd": row.get("cost_usd") or 0.0,
            "input_tokens": row.get("input_tokens") or 0,
            "output_tokens": row.get("output_tokens") or 0,
            "message_count": row.get("message_count") or 0,
            "tool_call_count": row.get("tool_call_count") or 0,
            "parent_session_id": row.get("parent_session_id"),
            "error": _is_error(row),
            "external": row.get("source") == "external",
            "profile": row.get("_profile"),
            "running": _is_running(row),
            "image_count": len(row.get("_images") or []),
        })
        if len(sessions) >= limit:
            break
    return {"sessions": sessions, "generated_at": time.time()}


@router.get("/prompt/{agent_key}")
async def get_agent_prompt(agent_key: str):
    if not agent_key.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid agent key")
    if agent_key.startswith("prof-"):
        prompt = _profile_prompt(agent_key[5:])
    else:
        prompt = _latest_prompt_for(agent_key)
    if prompt is None:
        raise HTTPException(status_code=404, detail="No system prompt recorded for this agent")
    return {"agent_key": agent_key, "system_prompt": prompt}


@router.get("/profile/{name}/messages/{session_id}")
async def get_profile_session_messages(name: str, session_id: str):
    conn = _open_profile_db(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Unknown profile")
    try:
        cur = conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, tool_name, timestamp"
            " FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        )
        messages = []
        for r in cur.fetchall():
            m: Dict[str, Any] = {"role": r["role"], "content": r["content"],
                                 "tool_call_id": r["tool_call_id"], "tool_name": r["tool_name"],
                                 "timestamp": r["timestamp"]}
            if r["tool_calls"]:
                try:
                    m["tool_calls"] = json.loads(r["tool_calls"])
                except Exception:
                    pass
            messages.append(m)
        return {"session_id": session_id, "messages": messages}
    finally:
        conn.close()


@router.post("/ingest")
async def ingest(body: Any = None):
    """Append external session records (single object or list)."""
    records = body if isinstance(body, list) else [body]
    accepted = 0
    lines: List[str] = []
    for rec in records:
        if isinstance(rec, dict) and normalize_external(rec) is not None:
            lines.append(json.dumps(rec, ensure_ascii=False))
            accepted += 1
    if lines:
        with _INGEST_LOCK:
            with _ingest_path().open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
    if not accepted:
        raise HTTPException(status_code=400, detail="No valid records (each needs at least agent + started_at)")
    return {"ok": True, "accepted": accepted}


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@router.get("/external/{rec_id}/image/{index}")
async def get_external_image(rec_id: str, index: int):
    """Serve one image declared by an ingested record (declared paths only)."""
    rec = _external_record(rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown external session")
    images = [str(p) for p in (rec.get("images") or []) if isinstance(p, str)]
    if not (0 <= index < len(images)):
        raise HTTPException(status_code=404, detail="No such image")
    path = Path(images[index])
    if path.suffix.lower() not in _IMAGE_SUFFIXES or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not available")
    if FileResponse is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="FileResponse unavailable")
    return FileResponse(str(path))


@router.get("/external/{rec_id}/messages")
async def get_external_messages(rec_id: str):
    msgs = _external_messages(rec_id)
    if msgs is None:
        raise HTTPException(status_code=404, detail="Unknown external session")
    return {"session_id": "ext:" + rec_id, "messages": msgs}


@router.post("/identity")
async def save_identity(body: Dict[str, Any]):
    key = str(body.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Missing agent key")
    allowed = {"name", "tagline", "skills", "color"}
    patch = {k: v for k, v in body.items() if k in allowed}
    if "skills" in patch and not isinstance(patch["skills"], list):
        raise HTTPException(status_code=400, detail="skills must be a list of strings")
    if "skills" in patch:
        patch["skills"] = [str(s).strip() for s in patch["skills"] if str(s).strip()][:8]
    with _IDENTITY_LOCK:
        identities = _load_identities()
        if body.get("reset"):
            identities.pop(key, None)
        else:
            current = identities.get(key) or {}
            current.update(patch)
            identities[key] = current
        _save_identities(identities)
    return {"ok": True, "identity": identities.get(key)}
