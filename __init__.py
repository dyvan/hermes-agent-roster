"""Agent Roster — dashboard-only Hermes plugin.

All functionality lives in ``dashboard/`` (manifest, IIFE frontend, FastAPI
backend). No agent hooks or tools are registered.
"""


def register(ctx):  # noqa: ARG001 - required plugin entry point
    """No-op: this plugin only extends the web dashboard."""
    return None
