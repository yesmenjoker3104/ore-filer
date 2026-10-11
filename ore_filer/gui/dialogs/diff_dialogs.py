from __future__ import annotations

import difflib

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# ── 差分ダイアログ ───────────────────────────────────────────


class DiffDialog(QDialog):
    def __init__(self, left_name: str, right_name: str, diff_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"差分: {left_name}  ↔  {right_name}")
        self.resize(960, 700)

        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)

        if diff_text:
            self._editor.setPlainText(diff_text)
            self._apply_diff_colors()
        else:
            self._editor.setPlainText("（差分なし: 同一内容です）")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._editor)

    def _apply_diff_colors(self) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        doc = self._editor.document()
        add_fmt = QTextCharFormat()
        add_fmt.setBackground(QColor("#1e3a1e"))
        add_fmt.setForeground(QColor("#b5e8b5"))
        del_fmt = QTextCharFormat()
        del_fmt.setBackground(QColor("#3a1e1e"))
        del_fmt.setForeground(QColor("#e8b5b5"))
        hunk_fmt = QTextCharFormat()
        hunk_fmt.setBackground(QColor("#1e2a3a"))
        hunk_fmt.setForeground(QColor("#9ac0e8"))
        selections = []
        block = doc.begin()
        while block.isValid():
            text = block.text()
            if text.startswith("@@"):
                fmt = hunk_fmt
            elif text.startswith("+") and not text.startswith("+++"):
                fmt = add_fmt
            elif text.startswith("-") and not text.startswith("---"):
                fmt = del_fmt
            else:
                block = block.next()
                continue
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(block)
            cur.select(QTextCursor.SelectionType.LineUnderCursor)
            sel.cursor = cur
            sel.format = fmt
            selections.append(sel)
            block = block.next()
        self._editor.setExtraSelections(selections)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


class SideBySideDiffDialog(QDialog):
    """左右並べの差分ダイアログ。"""

    def __init__(
        self,
        left_name: str,
        right_name: str,
        left_text: str,
        right_text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        has_diff = left_text != right_text
        title = f"差分: {left_name}  ↔  {right_name}"
        if not has_diff:
            title += "  （同一内容）"
        self.setWindowTitle(title)
        self.resize(1200, 700)

        left_lines = left_text.splitlines()
        right_lines = right_text.splitlines()

        # SequenceMatcher で行を整列
        aligned_left: list[tuple[int | None, str]] = []   # (lineno | None, text)
        aligned_right: list[tuple[int | None, str]] = []
        left_changed: list[bool] = []
        right_changed: list[bool] = []
        self._diff_blocks: list[int] = []  # 差分ブロック先頭の行インデックス

        matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)
        l_no = r_no = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    aligned_left.append((l_no + k + 1, left_lines[i1 + k]))
                    aligned_right.append((r_no + k + 1, right_lines[j1 + k]))
                    left_changed.append(False)
                    right_changed.append(False)
                l_no += i2 - i1
                r_no += j2 - j1
            else:
                self._diff_blocks.append(len(aligned_left))
                lc, rc = i2 - i1, j2 - j1
                for k in range(max(lc, rc)):
                    aligned_left.append(
                        (l_no + k + 1, left_lines[i1 + k]) if k < lc else (None, "")
                    )
                    aligned_right.append(
                        (r_no + k + 1, right_lines[j1 + k]) if k < rc else (None, "")
                    )
                    left_changed.append(tag in ("replace", "delete") or (tag == "insert" and k >= lc))
                    right_changed.append(tag in ("replace", "insert") or (tag == "delete" and k >= rc))
                l_no += lc
                r_no += rc

        max_no = max(
            max((ln for ln, _ in aligned_left if ln is not None), default=0),
            max((ln for ln, _ in aligned_right if ln is not None), default=0),
        )
        w = len(str(max_no)) if max_no > 0 else 1

        def fmt(lineno: int | None, text: str) -> str:
            if lineno is None:
                return " " * (w + 1) + " "
            return f"{lineno:{w}d} | {text}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)

        self._left_editor = QPlainTextEdit(self)
        self._left_editor.setReadOnly(True)
        self._left_editor.setFont(font)
        self._left_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._left_editor.setPlainText("\n".join(fmt(ln, t) for ln, t in aligned_left))

        self._right_editor = QPlainTextEdit(self)
        self._right_editor.setReadOnly(True)
        self._right_editor.setFont(font)
        self._right_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._right_editor.setPlainText("\n".join(fmt(ln, t) for ln, t in aligned_right))

        self._left_sels: list = self._apply_colors(self._left_editor, left_changed, aligned_left, is_right=False)
        self._right_sels: list = self._apply_colors(self._right_editor, right_changed, aligned_right, is_right=True)

        self._syncing = False
        for src, dst in (
            (self._left_editor, self._right_editor),
            (self._right_editor, self._left_editor),
        ):
            src.verticalScrollBar().valueChanged.connect(
                lambda v, d=dst: self._vsync(d, v)
            )
            src.horizontalScrollBar().valueChanged.connect(
                lambda v, d=dst: self._hsync(d, v)
            )

        self._cur_diff = -1

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        for editor, name in ((self._left_editor, left_name), (self._right_editor, right_name)):
            w_wrap = QWidget()
            vl = QVBoxLayout(w_wrap)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(0)
            vl.addWidget(QLabel(f"  {name}"))
            vl.addWidget(editor)
            splitter.addWidget(w_wrap)
        splitter.setSizes([600, 600])

        n = len(self._diff_blocks)
        base_hint = "N: 次の差分  P: 前の差分  Esc / Q: 閉じる"
        self._hint_label = QLabel(f"{base_hint}  ｜  差分 {n} 箇所", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._hint_label)

    def _apply_colors(
        self,
        editor: QPlainTextEdit,
        changed: list[bool],
        aligned: list[tuple[int | None, str]],
        *,
        is_right: bool,
    ) -> list:
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        del_fmt = QTextCharFormat()
        del_fmt.setBackground(QColor("#3a1e1e"))
        del_fmt.setForeground(QColor("#e8b5b5"))

        add_fmt = QTextCharFormat()
        add_fmt.setBackground(QColor("#1e3a1e"))
        add_fmt.setForeground(QColor("#b5e8b5"))

        pad_fmt = QTextCharFormat()
        pad_fmt.setBackground(QColor("#2a2a2a"))
        pad_fmt.setForeground(QColor("#555555"))

        selections = []
        block = editor.document().begin()
        for row, is_changed in enumerate(changed):
            if not block.isValid():
                break
            if is_changed:
                lineno = aligned[row][0]
                fmt = pad_fmt if lineno is None else (add_fmt if is_right else del_fmt)
                sel = QTextEdit.ExtraSelection()
                cur = QTextCursor(block)
                cur.select(QTextCursor.SelectionType.LineUnderCursor)
                sel.cursor = cur
                sel.format = fmt
                selections.append(sel)
            block = block.next()
        editor.setExtraSelections(selections)
        return selections

    def _vsync(self, target: QPlainTextEdit, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        target.verticalScrollBar().setValue(value)
        self._syncing = False

    def _hsync(self, target: QPlainTextEdit, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        target.horizontalScrollBar().setValue(value)
        self._syncing = False

    def _jump_to(self, idx: int) -> None:
        if not self._diff_blocks:
            return
        from PySide6.QtGui import QFont as _QFont
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        # カウンター更新
        n = len(self._diff_blocks)
        self._hint_label.setText(
            f"N: 次の差分  P: 前の差分  Esc / Q: 閉じる  ｜  差分 {idx + 1} / {n}"
        )

        # 現在ブロックの行範囲を特定（次の差分ブロック開始まで、または末尾まで）
        start_row = self._diff_blocks[idx]
        end_row = self._diff_blocks[idx + 1] if idx + 1 < n else 10**9

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#5a4a00"))
        cur_fmt.setForeground(QColor("#f0d040"))
        cur_fmt.setFontWeight(_QFont.Weight.Bold)

        def _current_sels(editor: QPlainTextEdit) -> list:
            sels = []
            doc = editor.document()
            for row in range(start_row, min(end_row, doc.blockCount())):
                blk = doc.findBlockByLineNumber(row)
                if not blk.isValid():
                    break
                sel = QTextEdit.ExtraSelection()
                c = QTextCursor(blk)
                c.select(QTextCursor.SelectionType.LineUnderCursor)
                sel.cursor = c
                sel.format = cur_fmt
                sels.append(sel)
            return sels

        self._left_editor.setExtraSelections(self._left_sels + _current_sels(self._left_editor))
        self._right_editor.setExtraSelections(self._right_sels + _current_sels(self._right_editor))

        block = self._left_editor.document().findBlockByLineNumber(start_row)
        if block.isValid():
            cur = QTextCursor(block)
            self._left_editor.setTextCursor(cur)
            self._left_editor.ensureCursorVisible()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        elif key == Qt.Key.Key_N and self._diff_blocks:
            self._cur_diff = (self._cur_diff + 1) % len(self._diff_blocks)
            self._jump_to(self._cur_diff)
        elif key == Qt.Key.Key_P and self._diff_blocks:
            self._cur_diff = (self._cur_diff - 1) % len(self._diff_blocks)
            self._jump_to(self._cur_diff)
        else:
            super().keyPressEvent(event)
