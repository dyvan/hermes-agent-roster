"""Hermetic unit tests for the agent-roster plugin backend (no network, no DB)."""
import importlib.util
import sys
import time
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "agent_roster_plugin_api",
    Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py",
)
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def _row(**kw):
    base = {
        "id": "s1", "source": "cli", "model": "hermes-4-405b", "title": "t",
        "started_at": time.time() - 100, "ended_at": time.time() - 40,
        "end_reason": None, "message_count": 4, "tool_call_count": 2,
        "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.05,
        "parent_session_id": None, "system_prompt_hash": "a" * 64,
        "display_name": None, "last_msg_at": None,
    }
    base.update(kw)
    return base


class AgentKeyTests(unittest.TestCase):
    def test_prompt_hash_wins(self):
        self.assertEqual(mod.agent_key_for("f" * 64, "cli", "m"), "f" * mod.AGENT_KEY_LEN)

    def test_fallback_is_stable_and_prefixed(self):
        k1 = mod.agent_key_for(None, "telegram", "hermes-4-70b")
        k2 = mod.agent_key_for(None, "telegram", "hermes-4-70b")
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("src-"))

    def test_different_sources_differ(self):
        self.assertNotEqual(
            mod.agent_key_for(None, "cli", "m"),
            mod.agent_key_for(None, "discord", "m"),
        )


class RosterTests(unittest.TestCase):
    def test_groups_by_prompt_hash(self):
        rows = [_row(id="a", system_prompt_hash="1" * 64),
                _row(id="b", system_prompt_hash="1" * 64),
                _row(id="c", system_prompt_hash="2" * 64)]
        agents = mod.build_roster(rows, {})
        self.assertEqual(len(agents), 2)
        runs = sorted(a["stats"]["month"]["runs"] for a in agents)
        self.assertEqual(runs, [1, 2])

    def test_period_buckets(self):
        now = time.time()
        rows = [
            _row(id="today", started_at=now - 60, ended_at=now - 10),
            _row(id="thisweek", started_at=now - 3 * 86400, ended_at=now - 3 * 86400 + 60),
            _row(id="thismonth", started_at=now - 20 * 86400, ended_at=now - 20 * 86400 + 60),
        ]
        agents = mod.build_roster(rows, {}, now=now)
        st = agents[0]["stats"]
        self.assertEqual(st["month"]["runs"], 3)
        self.assertEqual(st["week"]["runs"], 2)
        self.assertLessEqual(st["day"]["runs"], 2)
        self.assertGreaterEqual(st["day"]["runs"], 1)

    def test_identity_override_applied(self):
        rows = [_row(system_prompt_hash="3" * 64)]
        key = "3" * mod.AGENT_KEY_LEN
        agents = mod.build_roster(rows, {key: {"name": "Forge", "tagline": "code", "skills": ["python"]}})
        self.assertEqual(agents[0]["name"], "Forge")
        self.assertTrue(agents[0]["customized"])
        self.assertEqual(agents[0]["skills"], ["python"])

    def test_cost_and_duration_aggregation(self):
        now = time.time()
        rows = [_row(id="x", started_at=now - 100, ended_at=now - 40, cost_usd=0.05),
                _row(id="y", started_at=now - 300, ended_at=None, last_msg_at=now - 200, cost_usd=0.02)]
        agents = mod.build_roster(rows, {}, now=now)
        st = agents[0]["stats"]["month"]
        self.assertAlmostEqual(st["cost_usd"], 0.07, places=6)
        self.assertAlmostEqual(st["active_seconds"], 60 + 100, delta=1)

    def test_error_flag(self):
        self.assertTrue(mod._is_error({"end_reason": "tool_error"}))
        self.assertFalse(mod._is_error({"end_reason": "completed"}))
        self.assertFalse(mod._is_error({"end_reason": None}))


class ExternalIngestionTests(unittest.TestCase):
    def test_normalize_minimal(self):
        row = mod.normalize_external({"agent": "ql-runner", "started_at": 1000})
        self.assertEqual(row["source"], "external")
        self.assertEqual(row["display_name"], "ql-runner")
        self.assertTrue(row["id"].startswith("ext:"))
        self.assertEqual(row["_agent_key"], mod.external_key("ql-runner"))

    def test_normalize_rejects_missing_started_at(self):
        self.assertIsNone(mod.normalize_external({"agent": "x"}))

    def test_duration_from_seconds(self):
        row = mod.normalize_external({"agent": "a", "started_at": 1000, "duration_seconds": 45})
        self.assertEqual(row["ended_at"], 1045)

    def test_parent_and_error(self):
        row = mod.normalize_external({"agent": "a", "started_at": 1, "parent_id": "run-1", "error": True})
        self.assertEqual(row["parent_session_id"], "ext:run-1")
        self.assertTrue(mod._is_error(row))

    def test_key_stable_case_insensitive(self):
        self.assertEqual(mod.external_key("QL-Runner"), mod.external_key("ql-runner "))

    def test_roster_merges_external(self):
        now = time.time()
        rows = [_row(started_at=now - 60, ended_at=now - 10),
                mod.normalize_external({"agent": "ql-auteur", "started_at": now - 30,
                                        "duration_seconds": 20, "cost_usd": 0.01})]
        agents = mod.build_roster(rows, {}, now=now)
        names = {a["name"] for a in agents}
        self.assertIn("ql-auteur", names)


class GroupingTests(unittest.TestCase):
    def test_profile_sessions_nest_under_declared_run(self):
        now = time.time()
        run = mod.normalize_external({"id": "run-1", "agent": "ql-runner", "started_at": now - 600,
                                      "group_profiles": ["ql-auteur"]})
        inside = {"id": "prof:ql-auteur:s1", "_profile": "ql-auteur",
                  "started_at": now - 300, "parent_session_id": None}
        outside = {"id": "prof:ql-auteur:s0", "_profile": "ql-auteur",
                   "started_at": now - 900, "parent_session_id": None}
        other = {"id": "prof:ql-gardien:s2", "_profile": "ql-gardien",
                 "started_at": now - 300, "parent_session_id": None}
        rows = [run, inside, outside, other]
        mod.attach_profile_sessions_to_groups(rows, now=now)
        self.assertEqual(inside["parent_session_id"], "ext:run-1")
        self.assertIsNone(outside["parent_session_id"])
        self.assertIsNone(other["parent_session_id"])

    def test_latest_overlapping_run_wins(self):
        now = time.time()
        old = mod.normalize_external({"id": "r-old", "agent": "ql-runner", "started_at": now - 2000,
                                      "ended_at": now - 100, "group_profiles": ["ql-auteur"]})
        new = mod.normalize_external({"id": "r-new", "agent": "ql-runner", "started_at": now - 500,
                                      "group_profiles": ["ql-auteur"]})
        sess = {"id": "prof:ql-auteur:s1", "_profile": "ql-auteur",
                "started_at": now - 200, "parent_session_id": None}
        rows = [old, new, sess]
        mod.attach_profile_sessions_to_groups(rows, now=now)
        self.assertEqual(sess["parent_session_id"], "ext:r-new")


class DefaultNameTests(unittest.TestCase):
    def test_model_short_form(self):
        self.assertEqual(mod.default_name("cli", "org/hermes-4-405b:free", "abcd1234"), "CLI · hermes-4-405b")

    def test_no_model(self):
        self.assertEqual(mod.default_name("telegram", None, "abcd1234"), "TELEGRAM · abcd")


if __name__ == "__main__":
    unittest.main()
