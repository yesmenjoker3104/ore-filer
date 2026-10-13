"""ファイル操作の Undo サポート。"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class UndoEntry:
    current: Path
    original: Path | None  # None の場合は current をごみ箱へ（copy/create の undo）


@dataclass
class UndoRecord:
    kind: str        # "copy" | "move" | "rename" | "create" | "trash"
    label: str
    entries: list[UndoEntry] = field(default_factory=list)


class UndoStack:
    MAX_SIZE = 20

    def __init__(self) -> None:
        self._stack: list[UndoRecord] = []

    def push(self, record: UndoRecord) -> None:
        self._stack.append(record)
        if len(self._stack) > self.MAX_SIZE:
            self._stack.pop(0)

    def peek(self) -> UndoRecord | None:
        return self._stack[-1] if self._stack else None

    def pop(self) -> UndoRecord | None:
        return self._stack.pop() if self._stack else None

    def __len__(self) -> int:
        return len(self._stack)


def undo_operation(record: UndoRecord, *, progress_cb=None) -> list[str]:
    """Undo レコードを実行する。戻せなかった項目の説明リストを返す。"""
    from PySide6.QtCore import QFile

    skipped: list[str] = []
    total = len(record.entries)

    for i, entry in enumerate(record.entries):
        if progress_cb:
            progress_cb(i + 1, total)

        if entry.original is None:
            # copy/create の undo: current をごみ箱へ
            if not entry.current.exists():
                skipped.append(f"{entry.current.name}: 見つかりません")
                continue
            if not QFile(str(entry.current)).moveToTrash():
                skipped.append(f"{entry.current.name}: ごみ箱に移動できません")
        else:
            # move/rename/trash の undo: current → original に移動
            if not entry.current.exists():
                skipped.append(f"{entry.current.name}: 見つかりません")
                continue
            if entry.original.exists():
                skipped.append(f"{entry.original.name}: 移動先に既にファイルがあります")
                continue
            try:
                entry.original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry.current), str(entry.original))
            except OSError as e:
                skipped.append(f"{entry.current.name}: {e}")

    return skipped
