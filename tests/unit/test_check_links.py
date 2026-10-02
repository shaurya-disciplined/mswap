"""Unit tests for check_links.py scanner."""

from __future__ import annotations

from pathlib import Path

from scripts.check_links import check_markdown_file


def test_valid_links_pass(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    target1 = tmp_path / "target.md"
    target2 = tmp_path / "img.png"
    target1.write_text("# Target", encoding="utf-8")
    target2.write_bytes(b"fake-image")

    doc.write_text(
        """
# Doc
Check [Target](target.md) and [Target Section](target.md#heading).
External: [Google](https://google.com) and [Email](mailto:test@example.com).
In-page: [Top](#top).
Image: ![Alt](img.png).
HTML: <img src="img.png" />
""",
        encoding="utf-8",
    )

    errors = check_markdown_file(doc)
    assert errors == []


def test_broken_link_detected(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(
        """
# Doc
Broken: [Missing](nonexistent.md).
""",
        encoding="utf-8",
    )

    errors = check_markdown_file(doc)
    assert len(errors) == 1
    assert "broken relative link 'nonexistent.md'" in errors[0]
