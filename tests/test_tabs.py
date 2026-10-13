"""Phase 6: ペインごとのタブ機能テスト。"""
import os
import sys
from pathlib import Path
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


# ── MainWindow 生成テスト ───────────────────────────────────────


def test_main_window_has_tab_widgets(tmp_path):
    """MainWindow が _left_tabs / _right_tabs を持つ。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    assert hasattr(w, "_left_tabs")
    assert hasattr(w, "_right_tabs")
    w.close()


def test_left_right_pane_properties(tmp_path):
    """left_pane / right_pane プロパティが PaneWidget を返す。"""
    from ore_filer.gui.main_window import MainWindow
    from ore_filer.gui.pane import PaneWidget
    w = MainWindow(str(tmp_path), str(tmp_path))
    assert isinstance(w.left_pane, PaneWidget)
    assert isinstance(w.right_pane, PaneWidget)
    w.close()


def test_panes_returns_two_elements(tmp_path):
    """panes プロパティが 2 要素のリストを返す。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    assert len(w.panes) == 2
    w.close()


def test_initial_tab_count(tmp_path):
    """起動直後、左右それぞれタブが 1 枚。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    assert w._left_tabs.count() == 1
    assert w._right_tabs.count() == 1
    w.close()


# ── tab_new / tab_close ────────────────────────────────────────


def test_tab_new_increases_count(tmp_path):
    """tab_new() でアクティブ側のタブ数が増える。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    w.active_pane_index = 0
    w.tab_new()
    assert w._left_tabs.count() == 2
    w.close()


def test_tab_close_decreases_count(tmp_path):
    """tab_close() でタブ数が減る。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    w.active_pane_index = 0
    w.tab_new()
    assert w._left_tabs.count() == 2
    w.tab_close()
    assert w._left_tabs.count() == 1
    w.close()


def test_tab_close_does_not_close_last(tmp_path):
    """最後の 1 枚は閉じない。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    w.active_pane_index = 0
    assert w._left_tabs.count() == 1
    w.tab_close()
    assert w._left_tabs.count() == 1
    w.close()


# ── tab_next / tab_prev ───────────────────────────────────────


def test_tab_next_cycles(tmp_path):
    """tab_next() でタブが切り替わる。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    w.active_pane_index = 0
    w.tab_new()
    w._left_tabs.setCurrentIndex(0)
    w.tab_next()
    assert w._left_tabs.currentIndex() == 1
    w.tab_next()  # wrap around
    assert w._left_tabs.currentIndex() == 0
    w.close()


def test_tab_prev_cycles(tmp_path):
    """tab_prev() で逆方向に切り替わる。"""
    from ore_filer.gui.main_window import MainWindow
    w = MainWindow(str(tmp_path), str(tmp_path))
    w.active_pane_index = 0
    w.tab_new()
    w._left_tabs.setCurrentIndex(0)
    w.tab_prev()  # 0 → wrap → 1
    assert w._left_tabs.currentIndex() == 1
    w.close()


# ── keymap 登録確認 ───────────────────────────────────────────


def test_tab_actions_in_keymap():
    """4 つのタブアクションがキーマップに登録されている。"""
    from ore_filer.gui.keymap import KeyMap
    km = KeyMap()
    bindings = {action for action, _, _ in km.all_bindings()}
    assert "tab_new" in bindings
    assert "tab_close" in bindings
    assert "tab_next" in bindings
    assert "tab_prev" in bindings


# ── settings round-trip ───────────────────────────────────────


def test_save_load_session_tabs(tmp_path):
    """save_session / load_session でタブ情報が往復できる。"""
    import os
    orig_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp_path)
    try:
        from ore_filer.settings import save_session, load_session
        save_session(
            [],
            left_path="/left/a",
            right_path="/right/b",
            left_tabs=["/left/a", "/left/c"],
            right_tabs=["/right/b"],
            left_tab_index=1,
            right_tab_index=0,
        )
        sess = load_session()
        assert sess["left_tabs"] == ["/left/a", "/left/c"]
        assert sess["right_tabs"] == ["/right/b"]
        assert sess["left_tab_index"] == 1
        assert sess["right_tab_index"] == 0
    finally:
        if orig_appdata is None:
            del os.environ["APPDATA"]
        else:
            os.environ["APPDATA"] = orig_appdata


def test_save_session_default_tabs(tmp_path):
    """left_tabs 省略時は left_path から生成される。"""
    import os
    orig_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp_path)
    try:
        from ore_filer.settings import save_session, load_session
        save_session([], left_path="/foo", right_path="/bar")
        sess = load_session()
        assert sess["left_tabs"] == ["/foo"]
        assert sess["right_tabs"] == ["/bar"]
    finally:
        if orig_appdata is None:
            del os.environ["APPDATA"]
        else:
            os.environ["APPDATA"] = orig_appdata
