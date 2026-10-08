"""キーマップ管理。

デフォルトバインディングはこのファイルに内蔵。
ユーザーは %APPDATA%\\ore-filer\\keymap.toml を作成して上書きできる。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent


# ── キー名 → Qt.Key の対応表 ──────────────────────────────


_KEY_NAME_MAP: dict[str, Qt.Key] = {
    "A": Qt.Key.Key_A, "B": Qt.Key.Key_B, "C": Qt.Key.Key_C,
    "D": Qt.Key.Key_D, "E": Qt.Key.Key_E, "F": Qt.Key.Key_F,
    "G": Qt.Key.Key_G, "H": Qt.Key.Key_H, "I": Qt.Key.Key_I,
    "J": Qt.Key.Key_J, "K": Qt.Key.Key_K, "L": Qt.Key.Key_L,
    "M": Qt.Key.Key_M, "N": Qt.Key.Key_N, "O": Qt.Key.Key_O,
    "P": Qt.Key.Key_P, "Q": Qt.Key.Key_Q, "R": Qt.Key.Key_R,
    "S": Qt.Key.Key_S, "T": Qt.Key.Key_T, "U": Qt.Key.Key_U,
    "V": Qt.Key.Key_V, "W": Qt.Key.Key_W, "X": Qt.Key.Key_X,
    "Y": Qt.Key.Key_Y, "Z": Qt.Key.Key_Z,
    "0": Qt.Key.Key_0, "1": Qt.Key.Key_1, "2": Qt.Key.Key_2,
    "3": Qt.Key.Key_3, "4": Qt.Key.Key_4, "5": Qt.Key.Key_5,
    "6": Qt.Key.Key_6, "7": Qt.Key.Key_7, "8": Qt.Key.Key_8,
    "9": Qt.Key.Key_9,
    "F1": Qt.Key.Key_F1, "F2": Qt.Key.Key_F2, "F3": Qt.Key.Key_F3,
    "F4": Qt.Key.Key_F4, "F5": Qt.Key.Key_F5, "F6": Qt.Key.Key_F6,
    "F7": Qt.Key.Key_F7, "F8": Qt.Key.Key_F8, "F9": Qt.Key.Key_F9,
    "F10": Qt.Key.Key_F10, "F11": Qt.Key.Key_F11, "F12": Qt.Key.Key_F12,
    "Return": Qt.Key.Key_Return, "Enter": Qt.Key.Key_Return,
    "Backspace": Qt.Key.Key_Backspace,
    "Delete": Qt.Key.Key_Delete,
    "Space": Qt.Key.Key_Space,
    "Tab": Qt.Key.Key_Tab,
    "Escape": Qt.Key.Key_Escape,
    "Up": Qt.Key.Key_Up, "Down": Qt.Key.Key_Down,
    "Left": Qt.Key.Key_Left, "Right": Qt.Key.Key_Right,
    "Home": Qt.Key.Key_Home, "End": Qt.Key.Key_End,
    "PageUp": Qt.Key.Key_PageUp, "PageDown": Qt.Key.Key_PageDown,
    "Plus": Qt.Key.Key_Plus,
    "Minus": Qt.Key.Key_Minus,
    "Asterisk": Qt.Key.Key_Asterisk,
    "Backslash": Qt.Key.Key_Backslash,
    "Slash": Qt.Key.Key_Slash,
    "Question": Qt.Key.Key_Question,
    "Equal": Qt.Key.Key_Equal,
    "AsciiTilde": Qt.Key.Key_AsciiTilde,
}

_MOD_NAME_MAP: dict[str, Qt.KeyboardModifier] = {
    "Ctrl": Qt.KeyboardModifier.ControlModifier,
    "Shift": Qt.KeyboardModifier.ShiftModifier,
    "Alt": Qt.KeyboardModifier.AltModifier,
}

# アクション名 → 説明
ACTION_DESCRIPTIONS: dict[str, str] = {
    "enter":            "フォルダを開く / アーカイブを開く",
    "execute":          "関連付け実行（Ctrl+Enter）",
    "parent":           "親フォルダへ",
    "switch_pane":      "ペインを切り替える",
    "copy":             "コピー",
    "copy_names":       "ファイル名をクリップボードへ",
    "copy_paths":       "フルパスをクリップボードへ",
    "move":             "移動 / 新規フォルダ",
    "delete":           "完全削除",
    "trash":            "ごみ箱へ移動",
    "rename":           "名前の変更",
    "bulk_rename":      "一括リネーム（選択項目）",
    "history":          "フォルダ履歴",
    "bookmark":         "ブックマーク（カレント）",
    "bookmark_all":     "ブックマーク（全体）",
    "bookmark_add":     "ブックマークに追加",
    "filter":           "インクリメンタルフィルター",
    "search":           "ファイル名検索",
    "grep":             "テキスト GREP",
    "sort":             "ソート",
    "drive":            "ドライブ選択",
    "archive":          "アーカイブ作成",
    "extract":          "アーカイブ展開",
    "select_down":      "選択してカーソルを下へ",
    "select_up":        "選択してカーソルを上へ",
    "range_select":     "範囲選択（アンカー）",
    "select_all_files": "ファイルをすべて選択",
    "select_all":       "ファイル＋フォルダをすべて選択",
    "deselect":         "選択をすべて解除",
    "reload":           "再読み込み",
    "context_menu":     "シェルコンテキストメニュー",
    "file_info":        "ファイル情報",
    "folder_size":      "フォルダサイズ計算",
    "view_text":        "テキストビューア",
    "open_editor":      "外部エディタで開く",
    "set_editor":       "エディタを設定",
    "terminal":         "ターミナルを開く",
    "compare":          "左右ペイン比較選択",
    "wildcard":         "ワイルドカード選択",
    "jump_path":        "パスを入力して移動",
    "home_dir":         "ホームフォルダへ",
    "go_first":         "先頭へ（gg）",
    "go_last":          "末尾へ",
    "sync_panes":       "相手ペインのパスに移動",
    "sync_other":       "相手ペインをこちらに合わせる",
    "font_larger":      "フォントを大きく",
    "font_smaller":     "フォントを小さく",
    "keymap_help":      "キー一覧を表示",
    "update_check":     "バージョン情報 / アップデートを確認",
    "quit":             "アプリを終了",
    "expand_tree":      "ツリーを展開/折りたたむ",
    "refresh":          "F5 で再読み込み",
    "center_splitter":  "スプリッターを中央に",
    "new_file":         "新規空ファイルを作成",
    "explorer":         "エクスプローラーで開く",
    "toggle_hidden":    "隠しファイル表示を切り替える",
    "diff":             "左右ペインのファイルを差分表示",
}

# デフォルトバインディング（アクション名 → キースペック文字列）
DEFAULT_BINDINGS: dict[str, str] = {
    "enter":            "Return",
    "execute":          "Ctrl+Return",
    "parent":           "Backspace",
    "switch_pane":      "Tab",
    "copy":             "C",
    "copy_names":       "Ctrl+C",
    "copy_paths":       "Ctrl+Shift+C",
    "move":             "M",
    "delete":           "K",
    "trash":            "Shift+K",
    "rename":           "R",
    "bulk_rename":      "Shift+R",
    "history":          "H",
    "bookmark":         "B",
    "bookmark_all":     "Shift+B",
    "bookmark_add":     "Ctrl+B",
    "filter":           "F",
    "search":           "Shift+F",
    "grep":             "Ctrl+G",
    "sort":             "S",
    "drive":            "D",
    "archive":          "P",
    "extract":          "U",
    "select_down":      "Space",
    "select_up":        "Shift+Space",
    "range_select":     "Ctrl+Space",
    "select_all_files": "A",
    "select_all":       "Shift+A",
    "deselect":         "End",
    "reload":           "Shift+End",
    "context_menu":     "Backslash",
    "file_info":        "I",
    "folder_size":      "Shift+I",
    "view_text":        "V",
    "open_editor":      "E",
    "set_editor":       "Shift+E",
    "terminal":         "W",
    "compare":          "X",
    "wildcard":         "Asterisk",
    "jump_path":        "Shift+J",
    "home_dir":         "AsciiTilde",
    "go_first":         "G",
    "go_last":          "Shift+G",
    "sync_panes":       "O",
    "sync_other":       "Shift+O",
    "font_larger":      "Plus",
    "font_smaller":     "Minus",
    "keymap_help":      "Question",
    "update_check":     "Ctrl+Shift+U",
    "quit":             "Q",
    "expand_tree":      "T",
    "refresh":          "F5",
    "center_splitter":  "Equal",
    "new_file":         "N",
    "explorer":         "Shift+W",
    "toggle_hidden":    "Shift+H",
    "diff":             "Shift+X",
}


# ── 型エイリアス ──────────────────────────────────────────

KeySpec = tuple[Qt.Key, Qt.KeyboardModifier]  # (key, modifiers)


def _parse_spec(spec: str) -> KeySpec:
    """キースペック文字列を (Qt.Key, modifiers) に変換する。"""
    parts = [p.strip() for p in spec.split("+")]
    mods = Qt.KeyboardModifier.NoModifier
    for part in parts[:-1]:
        m = _MOD_NAME_MAP.get(part)
        if m is None:
            raise ValueError(f"不明な修飾キー: {part!r} (spec={spec!r})")
        mods |= m
    key_name = parts[-1]
    key = _KEY_NAME_MAP.get(key_name)
    if key is None:
        raise ValueError(f"不明なキー名: {key_name!r} (spec={spec!r})")
    return key, mods


def _spec_to_str(spec: str) -> str:
    """内部スペック文字列をそのまま返す（表示用）。"""
    return spec


# ── KeyMap クラス ─────────────────────────────────────────


class KeyMap:
    """キーマップの管理クラス。

    matches(event, action) でアクションとイベントを照合する。
    action_for(event) でイベントからアクション名を逆引きする。
    """

    def __init__(self) -> None:
        self._bindings: dict[str, KeySpec] = {}
        self._raw: dict[str, str] = {}        # action → spec 文字列（表示用）
        self._reverse: dict[KeySpec, str] = {}  # spec → action（逆引き）
        self.reload()

    def reload(self) -> None:
        """デフォルト + ユーザー設定を再読み込みする。"""
        raw: dict[str, str] = dict(DEFAULT_BINDINGS)

        # ユーザー設定ファイルを読み込んで上書き
        user_path = _user_keymap_file()
        if user_path.exists():
            try:
                import tomllib
                with user_path.open("rb") as f:
                    data = tomllib.load(f)
                user_keys = data.get("keys", {})
                if isinstance(user_keys, dict):
                    for action, spec in user_keys.items():
                        if isinstance(spec, str):
                            raw[action] = spec
            except Exception:
                pass  # 読み込み失敗はデフォルトにフォールバック

        # パースして登録
        bindings: dict[str, KeySpec] = {}
        reverse: dict[KeySpec, str] = {}
        for action, spec in raw.items():
            try:
                parsed = _parse_spec(spec)
                bindings[action] = parsed
                # 同じキーに複数アクションが割り当てられた場合は後勝ち
                reverse[parsed] = action
            except ValueError:
                pass  # 不正なスペックは無視

        self._bindings = bindings
        self._raw = raw
        self._reverse = reverse

    def matches(self, event: QKeyEvent, action: str) -> bool:
        """イベントが指定アクションのキーに一致するか判定する。"""
        spec = self._bindings.get(action)
        if spec is None:
            return False
        key, mods = spec
        return event.key() == key and event.modifiers() == mods

    def action_for(self, event: QKeyEvent) -> str | None:
        """イベントからアクション名を逆引きする。見つからなければ None。"""
        from PySide6.QtGui import QGuiApplication
        # Windows では event.modifiers() に Ctrl が欠けることがあるため補完する
        mods = event.modifiers() | QGuiApplication.keyboardModifiers()
        key = Qt.Key(event.key())
        result = self._reverse.get((key, mods))
        if result is not None:
            return result
        # ?、*、+、~ のように入力にShiftが必要な非英字キーは、
        # Shiftを除去して再検索する（Ctrl+? → Ctrl+Question など）
        is_letter = Qt.Key.Key_A <= key <= Qt.Key.Key_Z
        if not is_letter and (mods & Qt.KeyboardModifier.ShiftModifier):
            mods_without_shift = mods & ~Qt.KeyboardModifier.ShiftModifier
            return self._reverse.get((key, mods_without_shift))
        return None

    def all_bindings(self) -> list[tuple[str, str, str]]:
        """(action, key_spec_str, description) のリストを返す（表示用）。"""
        result = []
        for action, spec_str in self._raw.items():
            desc = ACTION_DESCRIPTIONS.get(action, "")
            result.append((action, spec_str, desc))
        result.sort(key=lambda t: t[0])
        return result


# ── ユーザー設定ファイルパス ──────────────────────────────


def _user_keymap_file() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "ore-filer" / "keymap.toml"


def user_keymap_file() -> Path:
    return _user_keymap_file()
