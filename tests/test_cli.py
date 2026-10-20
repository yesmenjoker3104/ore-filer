from __future__ import annotations

import json
from pathlib import Path

from ore_filer.cli.main import main


def test_list_supports_text_and_json(capsys, tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")

    assert main(["--format", "both", "list", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert str(tmp_path / "file.txt") in output
    assert '"name": "file.txt"' in output


def test_search_returns_json(capsys, tmp_path: Path) -> None:
    matching = tmp_path / "report.txt"
    matching.write_text("content", encoding="utf-8")
    (tmp_path / "other.bin").write_bytes(b"x")

    assert main(["--format", "json", "search", str(tmp_path), "report"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in payload] == ["report.txt"]


def test_delete_requires_confirmation(capsys, tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("content", encoding="utf-8")

    assert main(["delete", str(target)]) == 1
    assert target.exists()
    assert "--yes" in capsys.readouterr().err


def test_copy_and_rename(capsys, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("content", encoding="utf-8")
    destination.mkdir()

    assert main(["copy", str(source), str(destination)]) == 0
    copied = destination / source.name
    assert copied.read_text(encoding="utf-8") == "content"

    assert main(["rename", str(copied), "renamed.txt"]) == 0
    assert (destination / "renamed.txt").exists()
    capsys.readouterr()
