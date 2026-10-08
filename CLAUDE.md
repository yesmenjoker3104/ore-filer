# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 開発コマンド

プロジェクト内の `.venv` を使うこと。システムPythonへのインストール禁止。

```powershell
# 依存パッケージのインストール（初回 / 更新後）
.\.venv\Scripts\python.exe -m pip install -e .

# アプリ起動
.\.venv\Scripts\python.exe ore_filer/app.py

# 構文チェック（編集後に必ず実行）
.\.venv\Scripts\python.exe -m compileall ore_filer

# ヘッドレス環境でのMainWindow生成確認（PySide6変更後）
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -c "from PySide6.QtWidgets import QApplication; from ore_filer.gui.main_window import MainWindow; app=QApplication([]); MainWindow('.', '.'); print('SUCCESS')"
```

Windows版exeは GitHub Actions（`.github/workflows/build-windows.yml`）でPyInstallerを使いビルドする。ローカルビルド手順は同ファイルを参照。

## アーキテクチャ

### 層構成

```
キー入力 (eventFilter)
    ↓
コマンド (gui/commands.py, main_window.py のメソッド)
    ↓
共通サービス層 (services/)
    ↓
ファイルシステム
```

GUI・CLI・プラグインはすべて `services/` を経由してファイル操作を行う。GUIで直接ファイルIOしない。

### 主要コンポーネント

- **`ore_filer/app.py`** — `QApplication` とセッション復元を行うエントリーポイント。`ore_filer/app.py` を直接実行するか、PyInstallerでビルドして起動する
- **`gui/main_window.py`** — 左右ペインの管理、`eventFilter` でのキーハンドリング、ファイル操作の確認ダイアログ。長時間処理は `QThread` サブクラス（`ArchiveThread` 等）で非同期実行
- **`gui/pane.py`** — 1ペインのUI。`QFileSystemModel` + `QTreeView` でファイル一覧を表示。アーカイブ内閲覧モードと通常ディレクトリモードを切り替える
- **`services/file_operations.py`** — コピー・移動・削除・アーカイブ作成/展開の本体。zip/jar/apk は `pyzipper`、7z は `py7zr` を使用
- **`services/git_service.py`** — Git 操作サービス。`subprocess` で git CLI を呼び出す。依存パッケージ不要。`GIT_TERMINAL_PROMPT=0` で認証プロンプト待ちによるフリーズを防ぐ
- **`settings.py`** — セッション情報（ディレクトリ履歴）を `%APPDATA%\ore-filer\session.json` に、アプリ設定（エディタパス・隠しファイル表示等）を `%APPDATA%\ore-filer\config.json` に保存・復元

### キーマップ

キーバインディングは `config/keymap.toml`（リポジトリ同梱テンプレート）と `gui/keymap.py`（`KeyMap` クラス）で管理する。ユーザーは `%APPDATA%\ore-filer\keymap.toml` を作成して上書きでき、保存時にホットリロードされる。

`eventFilter` は `self._keymap.action_for(event)` でアクション名を取得してディスパッチする。新しいキー操作を追加する場合は次の4箇所を編集する:
1. `DEFAULT_BINDINGS`（`gui/keymap.py`）
2. `ACTION_DESCRIPTIONS`（`gui/keymap.py`）
3. `config/keymap.toml`（テンプレート）
4. `eventFilter` の `elif action == "xxx":` 分岐（`gui/main_window.py`）

### アーカイブ対応形式

zip / jar / apk / 7z / tar / tar.gz / tgz / tar.bz2 / tbz2。LZH・RARは未対応。アーカイブ内の閲覧・コピーも対応済み。

### プラグイン

`%APPDATA%\ore-filer\plugins\` に配置したPythonファイルを読み込む（将来実装）。`ore_filer/api/` が公開API層。プラグインはQtウィジェットを直接操作せず、`api/` 経由でアプリ機能を利用する。

## 変更方針

- 依頼されたファイルのみ変更する。既存コードの移動・削除・整理は明示的な依頼がない限り行わない
- 空の雛形ファイル（`ore_filer/api/*.py` など）には依頼された範囲だけ追加する
- 重い処理（コピー・展開等）はUIスレッドをブロックしないよう `QThread` で実行する
- 削除・上書きなど危険な操作は `QMessageBox.question` で確認を取る
