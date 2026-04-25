"""Unit tests for the agent-step output parser."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from memecoin.lib.agent_step import (  # noqa: E402
    AgentOutputParseError,
    parse_agent_output,
)


def test_parses_fenced_json_block():
    out = parse_agent_output("""\
some agent chatter

```json
{
  "primary_task": "investigate recalculatePortfolio",
  "outcome": "success",
  "summary": "wrote a recon doc",
  "files_changed": ["docs/agent-recon/2026-04-24-run2.md"],
  "blockers": []
}
```
""")
    assert out["primary_task"] == "investigate recalculatePortfolio"
    assert out["outcome"] == "success"
    assert out["files_changed"] == ["docs/agent-recon/2026-04-24-run2.md"]
    assert out["blockers"] == []


def test_uses_last_fenced_block_when_multiple():
    """If the agent shows the schema example earlier, we still pick the real one."""
    out = parse_agent_output("""\
Here's the schema:

```json
{"primary_task": "EXAMPLE", "outcome": "success"}
```

(actual run results below)

```json
{"primary_task": "real task", "outcome": "partial", "summary": "halfway"}
```
""")
    assert out["primary_task"] == "real task"
    assert out["outcome"] == "partial"


def test_falls_back_to_bare_json_object():
    out = parse_agent_output(
        'Final state: {"primary_task": "did the thing", "outcome": "success"}'
    )
    assert out["primary_task"] == "did the thing"


def test_raises_when_no_json_block():
    with pytest.raises(AgentOutputParseError, match="No JSON block found"):
        parse_agent_output("agent did its thing but forgot to print the schema")


def test_raises_on_invalid_json():
    with pytest.raises(AgentOutputParseError, match="not valid JSON"):
        parse_agent_output('```json\n{"primary_task": "bad json,}\n```')


def test_raises_when_primary_task_missing():
    with pytest.raises(AgentOutputParseError, match="primary_task"):
        parse_agent_output('```json\n{"outcome": "success"}\n```')


def test_raises_when_primary_task_empty():
    with pytest.raises(AgentOutputParseError, match="primary_task"):
        parse_agent_output('```json\n{"primary_task": "  ", "outcome": "success"}\n```')


def test_raises_on_bad_outcome():
    with pytest.raises(AgentOutputParseError, match="outcome"):
        parse_agent_output('```json\n{"primary_task": "x", "outcome": "wat"}\n```')


def test_normalizes_missing_optional_fields():
    out = parse_agent_output(
        '```json\n{"primary_task": "x", "outcome": "success"}\n```'
    )
    assert out["summary"] == ""
    assert out["files_changed"] == []
    assert out["blockers"] == []


def test_coerces_files_and_blockers_to_strings():
    out = parse_agent_output("""\
```json
{
  "primary_task": "x",
  "outcome": "blocked",
  "files_changed": ["a/b.py", "c/d.ts"],
  "blockers": ["test failed", "DB unreachable"]
}
```""")
    assert out["files_changed"] == ["a/b.py", "c/d.ts"]
    assert out["blockers"] == ["test failed", "DB unreachable"]
