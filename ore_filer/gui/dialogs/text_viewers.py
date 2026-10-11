from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# ── CSV ビューア ─────────────────────────────────────────


class CsvViewerDialog(QDialog):
    """CSV / TSV ファイルのテーブルビューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"CSV Viewer - {path.name}")
        self.resize(1000, 650)

        import csv as _csv

        delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            QMessageBox.critical(parent, "CSV Viewer", f"読み込みに失敗しました：\n{e}")
            return

        rows = list(_csv.reader(text.splitlines(), delimiter=delimiter))
        if not rows:
            rows = [[]]

        table = QTableWidget(len(rows) - 1, len(rows[0]), self)
        table.setHorizontalHeaderLabels(rows[0])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setDefaultSectionSize(22)

        for r, row in enumerate(rows[1:]):
            for c, cell in enumerate(row):
                if c < table.columnCount():
                    table.setItem(r, c, QTableWidgetItem(cell))

        row_label = QLabel(f"{len(rows) - 1} 行  |  {len(rows[0])} 列", self)
        hint = QLabel("Esc / Q: 閉じる", self)
        bottom = QHBoxLayout()
        bottom.addWidget(row_label)
        bottom.addStretch()
        bottom.addWidget(hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(table, 1)
        layout.addLayout(bottom)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── JSON ビューア ────────────────────────────────────────


class JsonViewerDialog(QDialog):
    """JSON / JSONL ファイルのフォーマット済みビューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"JSON Viewer - {path.name}")
        self.resize(960, 700)

        import json as _json

        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            text = f"読み込みに失敗しました: {e}"

        if path.suffix.casefold() == ".jsonl":
            lines = [l for l in text.splitlines() if l.strip()]
            parts = []
            for i, line in enumerate(lines):
                try:
                    parts.append(_json.dumps(_json.loads(line), ensure_ascii=False, indent=2))
                except _json.JSONDecodeError:
                    parts.append(line)
            formatted = "\n".join(parts)
        else:
            try:
                formatted = _json.dumps(_json.loads(text), ensure_ascii=False, indent=2)
            except _json.JSONDecodeError as e:
                formatted = f"// JSON パースエラー: {e}\n\n{text}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        editor = QPlainTextEdit(self)
        editor.setReadOnly(True)
        editor.setFont(font)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText(formatted)

        hint = QLabel("Esc / Q: 閉じる", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(editor, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── XML ビューア ─────────────────────────────────────────


class XmlViewerDialog(QDialog):
    """XML ファイルのフォーマット済みビューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"XML Viewer - {path.name}")
        self.resize(960, 700)

        import xml.dom.minidom as _minidom

        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
            formatted = _minidom.parseString(raw.encode("utf-8")).toprettyxml(indent="  ")
            # toprettyxml が先頭に余分な宣言行を追加する場合があるのでそのまま利用
        except Exception as e:  # noqa: BLE001
            formatted = f"<!-- XML パースエラー: {e} -->\n\n{raw if 'raw' in dir() else ''}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        editor = QPlainTextEdit(self)
        editor.setReadOnly(True)
        editor.setFont(font)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText(formatted)

        hint = QLabel("Esc / Q: 閉じる", self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(editor, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── Log ビューア ─────────────────────────────────────────


class LogViewerDialog(QDialog):
    """ログファイルのビューア（重大度で行を色分け）。"""

    _LEVELS = (
        (re.compile(r"\b(FATAL|CRITICAL|SEVERE)\b", re.IGNORECASE), "#5a1a1a", "#ff9090"),
        (re.compile(r"\bERROR\b",                   re.IGNORECASE), "#3a1e1e", "#e8b5b5"),
        (re.compile(r"\bWARN(ING)?\b",              re.IGNORECASE), "#3a2e00", "#f0d040"),
        (re.compile(r"\bINFO\b",                    re.IGNORECASE), "#1e2a3a", "#9ac0e8"),
        (re.compile(r"\bDEBUG\b",                   re.IGNORECASE), "#2a2a2a", "#888888"),
    )

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Log Viewer - {path.name}")
        self.resize(1100, 700)

        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            text = f"読み込みに失敗しました: {e}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        self._editor.setFont(font)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.setPlainText(text)
        self._apply_log_colors()

        hint = QLabel("Esc / Q: 閉じる", self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._editor, 1)
        layout.addWidget(hint)

    def _apply_log_colors(self) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        fmts = []
        for _, bg, fg in self._LEVELS:
            f = QTextCharFormat()
            f.setBackground(QColor(bg))
            f.setForeground(QColor(fg))
            fmts.append(f)

        selections = []
        block = self._editor.document().begin()
        while block.isValid():
            text = block.text()
            for i, (pat, _, _) in enumerate(self._LEVELS):
                if pat.search(text):
                    sel = QTextEdit.ExtraSelection()
                    cur = QTextCursor(block)
                    cur.select(QTextCursor.SelectionType.LineUnderCursor)
                    sel.cursor = cur
                    sel.format = fmts[i]
                    selections.append(sel)
                    break
            block = block.next()
        self._editor.setExtraSelections(selections)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── Hex ビューア ─────────────────────────────────────────


class HexViewerDialog(QDialog):
    """バイナリファイルの16進数ダンプビューア。"""

    _CHUNK = 65536  # 最大表示バイト数

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Hex Viewer - {path.name}")
        self.resize(900, 700)

        try:
            data = path.read_bytes()
            truncated = len(data) > self._CHUNK
            data = data[:self._CHUNK]
        except OSError as e:
            data = b""
            truncated = False
            notice = f"読み込みに失敗しました: {e}"
        else:
            notice = f"先頭 {self._CHUNK:,} バイトを表示  （合計: {path.stat().st_size:,} バイト）" if truncated else f"{len(data):,} バイト"

        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            hex_part = f"{hex_part:<47}"
            ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
            lines.append(f"{offset:08X}  {hex_part}  {ascii_part}")

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        editor = QPlainTextEdit(self)
        editor.setReadOnly(True)
        editor.setFont(font)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText("\n".join(lines))

        status = QLabel(notice, self)
        hint = QLabel("Esc / Q: 閉じる", self)
        bottom = QHBoxLayout()
        bottom.addWidget(status)
        bottom.addStretch()
        bottom.addWidget(hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(editor, 1)
        layout.addLayout(bottom)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── テキストビューア ──────────────────────────────────────


class TextViewerDialog(QDialog):
    def __init__(
        self,
        path: Path,
        text: str,
        encoding: str,
        highlight_lines: list[int] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._hits = sorted(set(highlight_lines)) if highlight_lines else []
        self._hit_index = 0
        self._path = path
        self._encoding = encoding
        self._grep_selections: list = []   # GREPハイライト
        self._search_selections: list = [] # テキスト検索ハイライト
        self.resize(900, 650)

        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)
        self._editor.setPlainText(text)

        # ── 検索バー（下部、非表示で開始）──
        self._search_bar = QWidget(self)
        bar_layout = QHBoxLayout(self._search_bar)
        bar_layout.setContentsMargins(4, 2, 4, 2)
        bar_layout.setSpacing(4)
        self._search_edit = QLineEdit(self._search_bar)
        self._search_edit.setPlaceholderText("検索 (Enter: 次へ  Shift+Enter: 前へ  Esc: 閉じる)")
        self._search_label = QLabel("", self._search_bar)
        self._search_label.setFixedWidth(80)
        bar_layout.addWidget(self._search_edit)
        bar_layout.addWidget(self._search_label)
        self._search_bar.setVisible(False)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._search_edit.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        layout.addWidget(self._editor)
        layout.addWidget(self._search_bar)

        if self._hits:
            self._apply_grep_highlights()
            self._jump_to_hit(0)
        else:
            self._editor.moveCursor(self._editor.textCursor().MoveOperation.Start)
        self._update_title()

    def _update_title(self) -> None:
        base = f"{self._path.name}  [{self._encoding}]"
        if self._hits:
            self.setWindowTitle(f"{base}  [{self._hit_index + 1}/{len(self._hits)}]")
        else:
            self.setWindowTitle(base)

    def _apply_grep_highlights(self) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#ffff99"))
        selections = []
        doc = self._editor.document()
        for lineno in self._hits:
            block = doc.findBlockByLineNumber(lineno - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(block)
            cur.select(QTextCursor.SelectionType.LineUnderCursor)
            sel.cursor = cur
            sel.format = fmt
            selections.append(sel)
        self._grep_selections = selections
        self._editor.setExtraSelections(self._grep_selections + self._search_selections)

    def _jump_to_hit(self, index: int) -> None:
        if not self._hits:
            return
        self._hit_index = max(0, min(index, len(self._hits) - 1))
        lineno = self._hits[self._hit_index]
        doc = self._editor.document()
        block = doc.findBlockByLineNumber(lineno - 1)
        if block.isValid():
            cur = self._editor.textCursor()
            cur.setPosition(block.position())
            self._editor.setTextCursor(cur)
            self._editor.centerCursor()
        self._update_title()

    # ── テキスト検索 ──────────────────────────────────────

    def _open_search_bar(self) -> None:
        self._search_bar.setVisible(True)
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def _close_search_bar(self) -> None:
        self._search_bar.setVisible(False)
        self._search_selections = []
        self._editor.setExtraSelections(self._grep_selections)
        self._search_label.setText("")
        self._editor.setFocus()

    def _on_search_text_changed(self, text: str) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        self._search_selections = []
        if not text:
            self._editor.setExtraSelections(self._grep_selections)
            self._search_label.setText("")
            return
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#b3d9ff"))
        doc = self._editor.document()
        cursor = QTextCursor(doc)
        count = 0
        while True:
            cursor = doc.find(text, cursor)
            if cursor.isNull():
                break
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            self._search_selections.append(sel)
            count += 1
        self._editor.setExtraSelections(self._grep_selections + self._search_selections)
        self._search_label.setText(f"{count} 件" if count else "見つからない")
        # 最初のマッチへスクロール
        if self._search_selections:
            self._editor.setTextCursor(self._search_selections[0].cursor)
            self._editor.centerCursor()

    def _search_next(self, backward: bool = False) -> None:
        from PySide6.QtGui import QTextDocument
        text = self._search_edit.text()
        if not text:
            return
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        if not self._editor.find(text, flags):
            # 折り返し
            cur = self._editor.textCursor()
            cur.movePosition(
                cur.MoveOperation.End if backward else cur.MoveOperation.Start
            )
            self._editor.setTextCursor(cur)
            self._editor.find(text, flags)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        no_mod = mods == Qt.KeyboardModifier.NoModifier
        if self._search_bar.isVisible():
            # 検索バーが開いているときは Esc だけ横取り
            if key == Qt.Key.Key_Escape:
                self._close_search_bar()
            else:
                super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_V,
                   Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        elif (key == Qt.Key.Key_F and mods == Qt.KeyboardModifier.ControlModifier) or \
             (key == Qt.Key.Key_Slash and no_mod):
            self._open_search_bar()
        elif key == Qt.Key.Key_N and self._hits and no_mod:
            self._jump_to_hit(self._hit_index + 1)
        elif key == Qt.Key.Key_N and self._hits and mods == Qt.KeyboardModifier.ShiftModifier:
            self._jump_to_hit(self._hit_index - 1)
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        """検索バーの Enter / Shift+Enter を横取りして前後検索。"""
        if watched is self._search_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._search_next(backward=mods == Qt.KeyboardModifier.ShiftModifier)
                return True
        return super().eventFilter(watched, event)
