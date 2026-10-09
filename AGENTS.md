# ore-filer 開発指示

## プロジェクト概要

Windows向けのキーボード操作中心の2画面ファイラー。cfilerの操作感を参考にしつつ、実装は新規に行う。詳細な設計、キー割り当て、各ファイルの役割は[README.md](README.md)を参照する。

## 技術スタック

- Python 3.12以上
- PySide6
- Dulwich（Git操作。`git.exe`は不要）
- setuptools（`pyproject.toml`）
- Windows + PowerShell

## Python環境

プロジェクト内の`.venv`を使用する。依存関係の追加・更新や検証は、プロジェクトルートで`.venv\Scripts\python.exe`を明示して実行する。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

システムPythonへ依存関係をインストールしない。VS Codeのインタープリターは`.venv\Scripts\python.exe`を使う。

## 構成と責務

- `ore_filer/app.py`: アプリケーション起動
- `ore_filer/gui/`: PySide6の画面、ペイン、キー操作、ダイアログ
- `ore_filer/services/`: GUI・CLI・プラグインで共有するファイル操作
- `ore_filer/services/git_service.py`: DulwichによるGit状態確認、ローカル操作、Fetch/Push
- `ore_filer/api/`: Pythonプラグイン向け公開API
- `ore_filer/cli/`: CLI入口と引数処理
- `config/keymap.toml`: 外部キー設定
- `plugins/`: ユーザー拡張

画面からファイル操作を直接実装せず、共通処理は`services`へ置く。GUI、CLI、プラグインで同じサービスを利用する。

## 変更方針

- ユーザーが明示的に依頼したファイルだけを変更する
- 既存コードを勝手に移動、削除、復元しない
- 空の雛形ファイルには、実装を依頼された範囲だけ追加する
- キー設定は `gui/keymap.py` の `DEFAULT_BINDINGS` と `config/keymap.toml` で管理する。`eventFilter` に直接キーコードを書かない
- ファイル操作はUIスレッドをブロックしない
- 削除、上書き、移動などの危険な操作は確認・キャンセルを考慮する
- 既存のcfilerコードは流用しない

## 起動・検証

現在のGUIを確認するには、`QApplication`を作成してから`MainWindow`を生成する。ヘッドレス環境では`QT_QPA_PLATFORM=offscreen`を設定する。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -c "from PySide6.QtWidgets import QApplication; from ore_filer.gui.main_window import MainWindow; app=QApplication([]); MainWindow('.', '.'); print('SUCCESS')"
```

Pythonソースの構文確認:

```powershell
.\.venv\Scripts\python.exe -m compileall ore_filer
```

編集後は、変更した範囲に近い検証を優先し、少なくとも構文確認を実行する。PySide6のGUI変更時は`MainWindow`生成確認も行う。
