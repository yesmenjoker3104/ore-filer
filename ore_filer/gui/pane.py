from __future__ import annotations

import posixpath
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QFileInfo,
    QItemSelectionModel,
    QModelIndex,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileIconProvider,
    QFileSystemModel,
    QVBoxLayout,
    QWidget,
)

from ore_filer.gui.pane_archive import PaneArchiveMixin
from ore_filer.gui.pane_remote import PaneRemoteMixin
from ore_filer.gui.pane_views import (
    SORT_DATE,
    SORT_EXT,
    SORT_NAME,
    SORT_SIZE,
    ElidedLabel,
    FileFilterProxyModel,
    FileItemDelegate,
    FileTreeView,
    SearchResultFilterProxyModel,
    _fmt_size,
)
from ore_filer.services.file_operations import (
    ArchiveEntry,
    disk_usage,
    list_archive_entries,
)
from ore_filer.services.remote_service import RemoteSession


class PaneWidget(PaneArchiveMixin, PaneRemoteMixin, QWidget):
    path_changed = Signal(str)
    selection_changed = Signal()
    current_item_changed = Signal()  # カーソル位置変化（プレビュー用）
    files_dropped = Signal(list, str, bool)  # (paths, destination, is_move)
    remote_error = Signal(str)

    def __init__(
        self,
        initial_path: str | Path,
        parent=None,
        history: list[str | Path] | None = None,
    ):
        super().__init__(parent)
        initial_path = Path(initial_path).expanduser()
        self.current_path = (
            initial_path.resolve() if initial_path.is_dir() else Path.home()
        )
        self._history = self._build_history(self.current_path, history)
        self.path_label = ElidedLabel()

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden)
        self.model.directoryLoaded.connect(self._on_directory_loaded)
        self.filter_model = FileFilterProxyModel(self)
        self.filter_model.setSourceModel(self.model)
        self.search_model = QStandardItemModel(self)
        self.search_filter_model = SearchResultFilterProxyModel(self)
        self.search_filter_model.setSourceModel(self.search_model)
        self._search_results: list[Path] | None = None
        self._grep_hits: dict[Path, list[int]] = {}
        self.archive_model = QStandardItemModel(self)
        self.archive_filter_model = SearchResultFilterProxyModel(self)
        self.archive_filter_model.setSourceModel(self.archive_model)
        self._archive_path: Path | None = None
        self._archive_member_dir = ""
        self._archive_password: str | None = None
        self._remote_session: RemoteSession | None = None
        self._remote_dir = ""
        self._remote_request_id = 0
        self._remote_focus_name: str | None = None
        self.remote_model = QStandardItemModel(self)
        self.remote_filter_model = SearchResultFilterProxyModel(self)
        self.remote_filter_model.setSourceModel(self.remote_model)
        self._sort_mode = SORT_NAME
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.bookmarks: set[str] = set()
        self._select_anchor: int | None = None
        self._cursor_memory: dict[Path, str] = {}
        self._pending_focus: str | None = None

        # Git 状態（casefolded パス文字列 → XY コード）
        self.git_status: dict[str, str] = {}
        self.git_branch: str | None = None

        self._font_size: int = self.font().pointSize()
        if self._font_size <= 0:
            self._font_size = 10

        self.file_view = FileTreeView()
        self.file_view.setModel(self.filter_model)
        self._reconnect_selection_signal()
        self.file_view.setColumnWidth(0, 300)
        self.file_view.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.file_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.file_view.setItemDelegate(FileItemDelegate(self.file_view))
        self.file_view.setStyleSheet(
            "QTreeView::item:selected { background-color: #3399ff; color: white; }"
        )
        self.file_view.setAlternatingRowColors(True)
        self.file_view.doubleClicked.connect(self._on_double_clicked)
        self.file_view.current_changed.connect(self.current_item_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.path_label)
        layout.addWidget(self.file_view)

        self.navigate_to(self.current_path, record_history=False)

    @staticmethod
    def _build_history(
        initial_path: Path, history: list[str | Path] | None
    ) -> list[Path]:
        paths = [Path(item).expanduser().resolve() for item in (history or [])]
        paths = [path for path in paths if path.is_dir()]
        paths = [path for path in paths if path != initial_path]
        paths.insert(0, initial_path)
        if history is not None:
            history[:] = paths[:200]
            return history
        return paths[:200]

    def navigate_to(
        self,
        path: str | Path,
        *,
        record_history: bool = True,
        focus_name: str | None = None,
    ) -> None:
        path = Path(path).resolve()
        if not path.is_dir():
            return
        self._select_anchor = None

        # 移動前のカーソル位置を記憶
        if not self.is_archive_view() and self._search_results is None:
            idx = self.file_view.currentIndex()
            cur = self.path_from_index(idx)
            if cur is not None:
                self._cursor_memory[self.current_path] = cur.name

        self._leave_archive_view()
        self._leave_remote_view()
        self.clear_search_results()
        self.clear_filter()
        if record_history and path != self.current_path:
            self._history[:] = [item for item in self._history if item != path]
            self._history.insert(0, path)
            del self._history[200:]

        self.current_path = path
        self._update_path_label()

        # フォーカス目標: 明示指定 > 記憶 > 先頭（""で先頭を表す）
        self._pending_focus = focus_name or self._cursor_memory.get(path) or ""

        # root index をクリアしてから root path を変更することで、
        # QFileSystemModel の再構築中に古い persistent index が
        # mapToSource に渡るのを防ぐ
        self.file_view.setRootIndex(QModelIndex())
        self.filter_model.set_root_path(path)
        root_index = self.model.setRootPath(str(path))
        proxy_root = self.filter_model.mapFromSource(root_index)
        if proxy_root.isValid():
            self.file_view.setRootIndex(proxy_root)
        self.filter_model.sort(0, self._sort_order)
        self.file_view.selectionModel().clearSelection()
        self.path_changed.emit(str(path))

        self._apply_pending_focus()

    def focus_name(self, name: str) -> None:
        self._pending_focus = name
        self._apply_pending_focus()
        if self._pending_focus is not None:
            QTimer.singleShot(300, self._apply_pending_focus)

    def _update_path_label(self) -> None:
        if self.is_remote_view():
            self.path_label.setText(self._remote_location())
            return
        labels = {SORT_NAME: "名前", SORT_EXT: "拡張子", SORT_SIZE: "サイズ", SORT_DATE: "日時"}
        arrow = "↑" if self._sort_order == Qt.SortOrder.AscendingOrder else "↓"
        prefix = f"[{labels[self._sort_mode]}{arrow}]"
        if self._search_results is not None:
            self.path_label.setText(f"{prefix} 検索結果: {self.current_path}")
        else:
            usage = disk_usage(self.current_path)
            if usage is not None:
                free, total = usage
                disk_info = f"  （空き {_fmt_size(free)} / {_fmt_size(total)}）"
            else:
                disk_info = ""
            branch_info = f"  [git: {self.git_branch}]" if self.git_branch else ""
            self.path_label.setText(f"{prefix} {self.current_path}{disk_info}{branch_info}")

    def set_git_info(self, branch: str | None, status_map: dict[str, str]) -> None:
        """Git の状態情報を更新してパス欄とリストを再描画する。"""
        self.git_status = status_map
        self.git_branch = branch
        self._update_path_label()
        self.file_view.viewport().update()

    def set_sort(self, mode: str, order: Qt.SortOrder) -> None:
        self._sort_mode = mode
        self._sort_order = order
        self.filter_model.set_sort_mode(mode)
        self.filter_model.sort(0, order)
        if self._search_results is not None:
            self.search_filter_model.set_sort_mode(mode)
            self.search_filter_model.sort(0, order)
        self._update_path_label()

    def sort_mode(self) -> str:
        return self._sort_mode

    def entry_from_index(self, index: QModelIndex) -> ArchiveEntry | None:
        if (
            not self.is_archive_view()
            or not index.isValid()
            or index.model() is not self.archive_filter_model
        ):
            return None
        value = index.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, ArchiveEntry) else None

    def focused_entry(self) -> ArchiveEntry | None:
        return self.entry_from_index(self.file_view.currentIndex())

    def selected_entries(self) -> list[ArchiveEntry]:
        if not self.is_archive_view():
            return []
        entries: list[ArchiveEntry] = []
        for index in self.file_view.selectionModel().selectedRows(0):
            entry = self.entry_from_index(index)
            if entry is not None:
                entries.append(entry)
        return entries

    def history_paths(self) -> list[Path]:
        return list(self._history)

    def set_filter(self, query: str) -> None:
        if self.is_remote_view():
            return
        if self.is_archive_view():
            current_entry = self.entry_from_index(self.file_view.currentIndex())
            self.archive_filter_model.set_query(query)
            self.file_view.selectionModel().clearSelection()
            if current_entry is not None:
                current_index = self.index_for_entry(current_entry)
                if current_index.isValid():
                    self.file_view.setCurrentIndex(current_index)
                    return
            if self.archive_filter_model.rowCount() > 0:
                self.file_view.setCurrentIndex(
                    self.archive_filter_model.index(0, 0)
                )
            return

        current_path = self.path_from_index(self.file_view.currentIndex())
        self.filter_model.set_query(query)
        self.search_filter_model.set_query(query)
        self.file_view.selectionModel().clearSelection()

        if self._search_results is not None:
            self.file_view.setRootIndex(QModelIndex())
            if current_path is not None:
                current_index = self.index_for_path(current_path)
                if current_index.isValid():
                    self.file_view.setCurrentIndex(current_index)
                    return

            if self.search_filter_model.rowCount() > 0:
                self.file_view.setCurrentIndex(
                    self.search_filter_model.index(0, 0)
                )
            return

        if current_path is not None:
            current_index = self.index_for_path(current_path)
            if current_index.isValid():
                self.file_view.setCurrentIndex(current_index)
                return

        root_index = self.filter_model.mapFromSource(
            self.model.index(str(self.current_path))
        )
        self.file_view.setRootIndex(root_index)
        if self.filter_model.rowCount(root_index) > 0:
            self.file_view.setCurrentIndex(
                self.filter_model.index(0, 0, root_index)
            )

    def clear_filter(self) -> None:
        if self.is_archive_view() and self.archive_filter_model.has_filter() or self.filter_model.has_filter():
            # フィルター解除後もカーソル位置を維持するため現在地を保存してから解除
            current_path = self.path_from_index(self.file_view.currentIndex())
            self.set_filter("")
            if current_path is not None:
                idx = self.index_for_path(current_path)
                if idx.isValid():
                    self.file_view.setCurrentIndex(idx)

    def show_search_results(
        self,
        paths: list[str | Path],
        line_hits: dict[Path, list[int]] | None = None,
    ) -> None:
        self._grep_hits = line_hits or {}
        self._search_results = [Path(path).expanduser().resolve() for path in paths]
        self.search_model.clear()
        self.search_model.setHorizontalHeaderLabels(["名前", "サイズ", "更新日時"])
        icon_provider = QFileIconProvider()
        for path in self._search_results:
            try:
                relative_path = path.relative_to(self.current_path)
            except ValueError:
                relative_path = path
            fi = QFileInfo(str(path))
            # 列0: 名前 + アイコン + フルパス
            name_item = QStandardItem(str(relative_path))
            name_item.setIcon(icon_provider.icon(fi))
            name_item.setData(str(path), Qt.ItemDataRole.UserRole)
            # 列1: サイズ（フォルダは空欄）
            if fi.isDir():
                size_text = ""
            else:
                size_text = _fmt_size(fi.size())
            size_item = QStandardItem(size_text)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # 列2: 更新日時
            dt = fi.lastModified()
            date_item = QStandardItem(dt.toString("yyyy-MM-dd HH:mm"))
            self.search_model.appendRow([name_item, size_item, date_item])

        self.search_filter_model.set_query(self.filter_model.query())
        self.search_filter_model.set_sort_mode(self._sort_mode)
        self.file_view.setModel(self.search_filter_model)
        self._reconnect_selection_signal()
        self.file_view.setRootIndex(QModelIndex())
        self.search_filter_model.sort(0, self._sort_order)
        self.file_view.selectionModel().clearSelection()
        header = self.file_view.header()
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        if self.search_filter_model.rowCount() > 0:
            self.file_view.setCurrentIndex(self.search_filter_model.index(0, 0))
        self._update_path_label()

    def clear_search_results(self) -> None:
        if self._search_results is None:
            return

        self._select_anchor = None
        self._search_results = None
        self._grep_hits = {}
        self.file_view.setRootIndex(QModelIndex())
        self.file_view.setModel(self.filter_model)
        self._reconnect_selection_signal()
        header = self.file_view.header()
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        root_index = self.filter_model.mapFromSource(
            self.model.index(str(self.current_path))
        )
        if root_index.isValid():
            self.file_view.setRootIndex(root_index)
        self.file_view.selectionModel().clearSelection()
        self._update_path_label()

    def has_search_results(self) -> bool:
        return self._search_results is not None

    def grep_lines(self, path: Path) -> list[int]:
        """GREP結果の該当行番号リストを返す（なければ空リスト）。"""
        return self._grep_hits.get(Path(path).resolve(), [])

    def set_show_hidden(self, show: bool) -> None:
        """隠しファイルの表示/非表示を切り替える。"""
        flags = QDir.AllEntries | QDir.NoDotAndDotDot
        if show:
            flags |= QDir.Hidden
        self.model.setFilter(flags)

    def has_filter(self) -> bool:
        if self.is_archive_view():
            return self.archive_filter_model.has_filter()
        return self.filter_model.has_filter()

    def filter_text(self) -> str:
        if self.is_archive_view():
            return self.archive_filter_model.query()
        return self.filter_model.query()

    def path_from_index(self, index: QModelIndex) -> Path | None:
        if not index.isValid():
            return None
        if self.is_archive_view() or self.is_remote_view():
            return None
        if self._search_results is not None:
            if index.model() is not self.search_filter_model:
                return None
            # UserRole は列0にのみ設定されているため列0に正規化する
            col0 = index.sibling(index.row(), 0)
            value = col0.data(Qt.ItemDataRole.UserRole)
            return Path(value) if value else None
        if index.model() is not self.filter_model:
            return None
        source_index = self.filter_model.mapToSource(index)
        if not source_index.isValid():
            return None
        return Path(self.model.filePath(source_index))

    def index_for_path(self, path: str | Path) -> QModelIndex:
        if self.is_archive_view() or self.is_remote_view():
            return QModelIndex()
        if self._search_results is not None:
            target = Path(path).expanduser().resolve()
            for row in range(self.search_filter_model.rowCount()):
                index = self.search_filter_model.index(row, 0)
                if self.path_from_index(index) == target:
                    return index
            return QModelIndex()

        source_index = self.model.index(str(path))
        if not source_index.isValid():
            return QModelIndex()
        return self.filter_model.mapFromSource(source_index)

    def is_dir(self, index: QModelIndex) -> bool:
        if self.is_archive_view():
            entry = self.entry_from_index(index)
            return entry is not None and entry.is_dir
        if self.is_remote_view():
            remote_entry = self.remote_entry_from_index(index)
            return remote_entry is not None and remote_entry.is_dir
        path = self.path_from_index(index)
        return path is not None and path.is_dir()

    def index_for_entry(self, entry: ArchiveEntry) -> QModelIndex:
        if not self.is_archive_view():
            return QModelIndex()
        for row in range(self.archive_filter_model.rowCount()):
            index = self.archive_filter_model.index(row, 0)
            if self.entry_from_index(index) == entry:
                return index
        return QModelIndex()

    def reveal_path(self, path: str | Path) -> bool:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            return False

        if path.is_dir():
            self.navigate_to(path)
            return True

        self.navigate_to(path.parent)
        index = self.index_for_path(path)
        if not index.isValid():
            return False
        self.file_view.setCurrentIndex(index)
        self.file_view.scrollTo(
            index,
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        return True

    def go_to_parent(self) -> None:
        if self.is_remote_view():
            parent = posixpath.dirname(self._remote_dir.rstrip("/")) or "/"
            if parent != self._remote_dir:
                self._remote_focus_name = posixpath.basename(self._remote_dir.rstrip("/"))
                self._request_remote_listing(parent)
            return
        if self.is_archive_view():
            assert self._archive_path is not None
            if self._archive_member_dir:
                parent_dir = self._archive_member_dir.rpartition("/")[0]
                entries = list_archive_entries(
                    self._archive_path,
                    parent_dir,
                    password=self._archive_password,
                )
                self._archive_member_dir = parent_dir
                self._show_archive_entries(entries)
                return

            archive_path = self._archive_path
            self._leave_archive_view()
            self.navigate_to(archive_path.parent, focus_name=archive_path.name)
            return

        parent_path = self.current_path.parent
        if parent_path != self.current_path:
            self.navigate_to(parent_path, focus_name=self.current_path.name)

    def go_to_first_item(self) -> None:
        root = self.file_view.rootIndex()
        first = self.file_view.model().index(0, 0, root)
        if first.isValid():
            self.file_view.setCurrentIndex(first)
            self.file_view.scrollTo(first)

    def go_to_last_item(self) -> None:
        root = self.file_view.rootIndex()
        count = self.file_view.model().rowCount(root)
        if count > 0:
            last = self.file_view.model().index(count - 1, 0, root)
            if last.isValid():
                self.file_view.setCurrentIndex(last)
                self.file_view.scrollTo(last)

    def move_page_up(self) -> None:
        """カーソルを1ページ分上へ移動する。"""
        self._move_page(QAbstractItemView.CursorAction.MovePageUp)

    def move_page_down(self) -> None:
        """カーソルを1ページ分下へ移動する。"""
        self._move_page(QAbstractItemView.CursorAction.MovePageDown)

    def _move_page(self, action: QAbstractItemView.CursorAction) -> None:
        index = self.file_view.moveCursor(
            action,
            Qt.KeyboardModifier.NoModifier,
        )
        if index.isValid():
            self.file_view.setCurrentIndex(index)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if self.is_remote_view():
            remote_entry = self.remote_entry_from_index(index)
            if remote_entry is not None:
                self.enter_remote_directory(remote_entry)
            return
        if self.is_archive_view():
            entry = self.entry_from_index(index)
            if entry is not None and entry.is_dir:
                self.enter_archive_directory(entry)
            return
        path = self.path_from_index(index)
        if path is not None and path.is_dir():
            self.navigate_to(path)

    def reload(self) -> None:
        if self.is_remote_view():
            self.reload_remote()
            return
        if self.is_archive_view():
            assert self._archive_path is not None
            entries = list_archive_entries(
                self._archive_path,
                self._archive_member_dir,
                password=self._archive_password,
            )
            self._show_archive_entries(entries)
            return
        self.navigate_to(self.current_path)

    def selected_paths(self) -> list[Path]:
        if self.is_archive_view() or self.is_remote_view():
            return []
        indexes = self.file_view.selectionModel().selectedRows(0)
        return [
            path
            for index in indexes
            if (path := self.path_from_index(index)) is not None
        ]

    def toggle_current_selection(self) -> None:
        index = self.file_view.currentIndex()
        if not index.isValid():
            return

        selection_model = self.file_view.selectionModel()
        selection_flag = QItemSelectionModel.SelectionFlag.Rows
        if selection_model.isSelected(index):
            selection_flag |= QItemSelectionModel.SelectionFlag.Deselect
        else:
            selection_flag |= QItemSelectionModel.SelectionFlag.Select

        selection_model.select(index, selection_flag)
        self.file_view.viewport().update()

    def deselect_all(self) -> None:
        self.file_view.selectionModel().clearSelection()
        self.file_view.viewport().update()

    def _iter_selectable_indexes(self, include_dirs: bool) -> list[QModelIndex]:
        model = self.file_view.model()
        if model is None:
            return []
        root = self.file_view.rootIndex()
        result = []
        for row in range(model.rowCount(root)):
            index = model.index(row, 0, root)
            path = self.path_from_index(index)
            if path is None or path.name == "..":
                continue
            if not include_dirs and path.is_dir():
                continue
            result.append(index)
        return result

    def select_all_files(self) -> None:
        """ファイルのみ全選択。全選択済みの場合は選択解除（トグル）。"""
        indexes = self._iter_selectable_indexes(include_dirs=False)
        if not indexes:
            return
        sel = self.file_view.selectionModel()
        all_selected = all(sel.isSelected(i) for i in indexes)
        if all_selected:
            sel.clearSelection()
        else:
            for i in indexes:
                sel.select(i, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.file_view.viewport().update()

    def select_all_items(self) -> None:
        """ファイル＋ディレクトリを全選択。全選択済みの場合は選択解除（トグル）。"""
        indexes = self._iter_selectable_indexes(include_dirs=True)
        if not indexes:
            return
        sel = self.file_view.selectionModel()
        all_selected = all(sel.isSelected(i) for i in indexes)
        if all_selected:
            sel.clearSelection()
        else:
            for i in indexes:
                sel.select(i, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.file_view.viewport().update()

    def select_current_and_move_down(self) -> None:
        self.toggle_current_selection()

        current_index = self.file_view.currentIndex()
        next_index = self.file_view.indexBelow(current_index)
        if next_index.isValid():
            self.file_view.setCurrentIndex(next_index)

    def select_current_and_move_up(self) -> None:
        self.toggle_current_selection()
        current_index = self.file_view.currentIndex()
        prev_index = self.file_view.indexAbove(current_index)
        if prev_index.isValid():
            self.file_view.setCurrentIndex(prev_index)

    def range_select(self) -> None:
        """Ctrl+Space: アンカー設定、または アンカー〜カーソル間を範囲選択。"""
        index = self.file_view.currentIndex()
        if not index.isValid():
            return
        current_row = index.row()
        if self._select_anchor is None:
            self._select_anchor = current_row
            self.file_view.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        else:
            anchor_row = self._select_anchor
            self._select_anchor = None
            model = self.file_view.model()
            root = self.file_view.rootIndex()
            if model is None:
                return
            lo, hi = min(anchor_row, current_row), max(anchor_row, current_row)
            sel = self.file_view.selectionModel()
            for row in range(lo, hi + 1):
                idx = model.index(row, 0, root)
                if idx.isValid():
                    path = self.path_from_index(idx)
                    if path is None or path.name != "..":
                        sel.select(
                            idx,
                            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                        )
        self.file_view.viewport().update()

    def _apply_pending_focus(self) -> None:
        if self._pending_focus is None:
            return
        root = self.file_view.rootIndex()
        model = self.file_view.model()
        if model is None or model.rowCount(root) == 0:
            return  # directoryLoaded で再試行

        target = self._pending_focus
        self._pending_focus = None

        if target:
            target_lower = target.casefold()
            for row in range(model.rowCount(root)):
                index = model.index(row, 0, root)
                path = self.path_from_index(index)
                if path and path.name.casefold() == target_lower:
                    self.file_view.setCurrentIndex(index)
                    self.file_view.scrollTo(index)
                    return

        first = model.index(0, 0, root)
        if first.isValid():
            self.file_view.setCurrentIndex(first)
            self.file_view.scrollTo(first)

    def _on_directory_loaded(self, loaded_path: str) -> None:
        if self._pending_focus is None:
            return
        try:
            if Path(loaded_path).resolve() == self.current_path:
                self._apply_pending_focus()
        except Exception:  # noqa: BLE001, S110
            pass

    def _reconnect_selection_signal(self) -> None:
        sm = self.file_view.selectionModel()
        if sm is None:
            return
        if getattr(self, "_sel_sm_connected", None) is sm:
            try:
                sm.selectionChanged.disconnect(self.selection_changed)
            except (TypeError, RuntimeError):
                pass
        sm.selectionChanged.connect(self.selection_changed)
        self._sel_sm_connected = sm

    def adjust_font_size(self, delta: int) -> None:
        """フォントとアイコンサイズを delta pt 変更する（8〜32pt の範囲）。"""
        new_size = max(8, min(32, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        font = self.file_view.font()
        font.setPointSize(new_size)
        self.file_view.setFont(font)
        icon_size = max(12, new_size + 4)
        self.file_view.setIconSize(QSize(icon_size, icon_size))
        # 行の高さを font に合わせて再計算させる
        self.file_view.scheduleDelayedItemsLayout()

    def visible_item_count(self) -> int:
        model = self.file_view.model()
        if model is None:
            return 0
        return model.rowCount(self.file_view.rootIndex())

    def selected_count(self) -> int:
        return len(self.file_view.selectionModel().selectedRows(0))

    def selected_total_size(self) -> int:
        total = 0
        for path in self.selected_paths():
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return total

    def select_paths(self, paths: list[Path]) -> None:
        """指定パスに一致する行を選択に追加する（名前は casefold で比較）。"""
        targets = {p.name.casefold() for p in paths}
        sel = self.file_view.selectionModel()
        for index in self._iter_selectable_indexes(include_dirs=True):
            path = self.path_from_index(index)
            if path and path.name.casefold() in targets:
                sel.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self.file_view.viewport().update()

    def select_by_pattern(self, patterns: list[str]) -> int:
        """ワイルドカードパターンに一致する行を選択し、選択件数を返す。"""
        import fnmatch
        count = 0
        sel = self.file_view.selectionModel()
        for index in self._iter_selectable_indexes(include_dirs=True):
            path = self.path_from_index(index)
            if path is None:
                continue
            name_lower = path.name.casefold()
            if any(fnmatch.fnmatch(name_lower, pat.strip().casefold()) for pat in patterns):
                sel.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                count += 1
        self.file_view.viewport().update()
        return count
