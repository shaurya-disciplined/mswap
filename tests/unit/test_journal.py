"""Unit tests for switch transaction Journal and Weave state machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mswap.core.errors import CorruptState
from mswap.core.journal import Journal


def test_journal_initial_state_none(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.json")
    assert journal.state() is None


def test_journal_happy_path(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = Journal(path)

    journal.begin("switch", from_fp="fp1", to_fp="fp2", live_fp="fp1")
    assert path.exists()

    st = journal.state()
    assert st is not None
    assert st["op"] == "switch"
    assert st["state"] == "begun"
    assert st["from_fp"] == "fp1"
    assert st["to_fp"] == "fp2"
    assert st["live_fp"] == "fp1"
    assert "at" in st

    journal.commit()
    st_committed = journal.state()
    assert st_committed is not None
    assert st_committed["state"] == "committed"

    journal.clear()
    assert not path.exists()
    assert journal.state() is None


def test_journal_fail_flow(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = Journal(path)

    journal.begin("switch", from_fp="fp1", to_fp="fp2", live_fp="fp1")
    journal.fail()

    st = journal.state()
    assert st is not None
    assert st["state"] == "failed"

    journal.clear()
    assert not path.exists()


def test_journal_begin_while_already_begun_raises(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.json")
    journal.begin("switch", from_fp="fp1", to_fp="fp2", live_fp="fp1")

    with pytest.raises(CorruptState) as exc_info:
        journal.begin("switch", from_fp="fp2", to_fp="fp3", live_fp="fp2")
    assert "Interrupted switch transaction in journal." in exc_info.value.message


def test_journal_commit_without_begun_raises(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.json")

    # None state
    with pytest.raises(CorruptState) as exc_info:
        journal.commit()
    assert "Cannot commit journal when state is 'none'." in exc_info.value.message

    # Committed state
    journal.begin("switch", from_fp=None, to_fp="fp2", live_fp=None)
    journal.commit()
    with pytest.raises(CorruptState) as exc_info:
        journal.commit()
    assert "Cannot commit journal when state is 'committed'." in exc_info.value.message


def test_journal_fail_without_begun_raises(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.json")

    # None state
    with pytest.raises(CorruptState) as exc_info:
        journal.fail()
    assert "Cannot fail journal when state is 'none'." in exc_info.value.message

    # Failed state
    journal.begin("switch", from_fp=None, to_fp="fp2", live_fp=None)
    journal.fail()
    with pytest.raises(CorruptState) as exc_info:
        journal.fail()
    assert "Cannot fail journal when state is 'failed'." in exc_info.value.message


def test_journal_clear_when_begun_raises(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.json")
    journal.begin("switch", from_fp="fp1", to_fp="fp2", live_fp="fp1")

    with pytest.raises(CorruptState) as exc_info:
        journal.clear()
    assert "Cannot clear an in-flight switch transaction." in exc_info.value.message


def test_journal_corrupt_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = Journal(path)

    # Not valid JSON
    path.write_text("not json content", encoding="utf-8")
    with pytest.raises(CorruptState) as exc_info:
        journal.state()
    assert "Interrupted switch transaction in journal is corrupt." in exc_info.value.message

    # Not a dict
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CorruptState):
        journal.state()

    # Missing or invalid state
    path.write_text(json.dumps({"op": "switch", "state": "unknown"}), encoding="utf-8")
    with pytest.raises(CorruptState):
        journal.state()

    # Missing or invalid op
    path.write_text(json.dumps({"op": 123, "state": "begun"}), encoding="utf-8")
    with pytest.raises(CorruptState):
        journal.state()
