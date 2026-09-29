# ore-filer

Windows向けの、キーボード操作中心の2画面ファイラー。

cfilerの操作感を参考にしつつ、実装は新規に行う。GUIにはPySide6を使用し、将来的なCLI操作、assist-chat連携、Pythonプラグインによる機能追加に対応する。

## 設計方針

- 既存のcfilerコードは流用しない
- GUIはPython + PySide6で実装する
- 標準キー操作はcfilerの106キーボード設定に準拠する
- キー設定はコードに埋め込まず、外部TOMLファイルで管理する
- GUIとCLIは同じファイル操作サービスを利用する
- Pythonプラグインから機能を追加できるようにする
- ファイル操作は画面をブロックしない
- 削除、上書きなどの危険な操作は明示的に確認できるようにする

## 画面構成

```text
+------------------------------------------------+
| 左ペインのパス       | 右ペインのパス          |
+----------------------+-------------------------+
| ..                   | ..                      |
| Documents            | Downloads               |
| sample.txt           | image.png               |
|                      |                         |
+----------------------+-------------------------+
| ステータスバー / 操作メッセージ                |
+------------------------------------------------+
```

- 左右のペインは同じ部品を使用する
- アクティブペインを1つ保持する
- コピー・移動先は、反対側ペインの現在ディレクトリを標準とする
- ペインの幅は変更可能にする
- ファイル一覧にはファイル名、サイズ、更新日時、ディレクトリを表示する

## 技術構成

```text
ore-filer/
├── README.md
├── pyproject.toml
├── ore_filer/
│   ├── app.py
│   ├── settings.py
│   ├── services/
│   │   ├── file_service.py
│   │   ├── directory_service.py
│   │   ├── search_service.py
│   │   ├── file_model.py
│   │   └── file_operations.py
│   ├── api/
│   │   ├── app.py
│   │   ├── command.py
│   │   ├── context.py
│   │   └── filesystem.py
│   ├── gui/
│   │   ├── main_window.py
│   │   ├── pane.py
│   │   ├── dialogs.py
│   │   ├── keymap.py
│   │   └── commands.py
│   └── cli/
│       └── main.py
├── config/
│   └── keymap.toml
└── plugins/
    └── sample_plugin/
        ├── plugin.py
        └── README.md
```

## ソースファイルの役割

### ルート

| ファイル | 役割 |
|---|---|
| `ore_filer/app.py` | アプリケーションの起動処理。`QApplication`とメインウィンドウを作成する |
| `ore_filer/settings.py` | アプリケーション設定、ユーザー設定、設定ファイルの管理 |

### `ore_filer/gui/`

画面表示とユーザー入力を担当する。ファイル操作の本体は`services`を呼び出す。

| ファイル | 役割 |
|---|---|
| `main_window.py` | メインウィンドウ、左右ペイン、アクティブペインの管理 |
| `pane.py` | 1つのペイン、ファイル一覧、フォルダ移動、選択状態の管理 |
| `dialogs.py` | 確認、入力、エラー表示などのダイアログ |
| `keymap.py` | キー入力とコマンドの対応、外部キー設定の読み込み |
| `commands.py` | GUIから実行するコマンドの定義と処理の振り分け |

### `ore_filer/services/`

GUI、CLI、プラグインから共通利用するファイル操作の本体を担当する。

| ファイル | 役割 |
|---|---|
| `file_service.py` | ファイルのコピー、移動、削除、リネーム |
| `directory_service.py` | ディレクトリの作成、列挙、移動 |
| `search_service.py` | ファイル名検索、内容検索、検索結果の管理 |
| `file_model.py` | ファイルやディレクトリの情報、一覧データのモデル |
| `file_operations.py` | 時間のかかるファイル操作、進捗、キャンセルの共通処理 |

### `ore_filer/api/`

Pythonプラグインや外部連携向けの安定した公開APIを担当する。

| ファイル | 役割 |
|---|---|
| `app.py` | プラグインからアプリケーション機能へアクセスするAPI |
| `command.py` | コマンドの基底クラスとコマンド登録API |
| `context.py` | アクティブペイン、選択項目、通知などの実行コンテキスト |
| `filesystem.py` | プラグインからファイル操作サービスを利用するAPI |

### `ore_filer/cli/`

コマンドラインからファイル操作を実行する機能を担当する。

| ファイル | 役割 |
|---|---|
| `main.py` | CLIの引数解析、サービス呼び出し、結果と終了コードの出力 |

### `plugins/`

ユーザーがPythonで機能を追加するためのプラグインを配置する。

| ファイル | 役割 |
|---|---|
| `sample_plugin/plugin.py` | プラグインのサンプル実装 |
| `sample_plugin/README.md` | プラグインの使い方とAPI説明 |

### `config/`

| ファイル | 役割 |
|---|---|
| `keymap.toml` | cfiler準拠の標準キー割り当て |

ファイル一覧は、まずQt標準の`QFileSystemModel`と`QTreeView`を利用する。コピー、移動、検索、再帰的な削除などの時間がかかる処理はワーカースレッドで実行する。

## 開発フェーズ

### Phase 1: ファイラーの骨格

- PySide6でウィンドウを表示
- 左右2ペイン
- ドライブ、フォルダ、親ディレクトリへの移動
- カーソル移動
- アクティブペインの切り替え
- ファイルの選択状態
- 再読み込み

### Phase 2: 基本ファイル操作

- コピー
- 移動
- リネーム
- ディレクトリ作成
- ごみ箱への削除
- 完全削除
- 操作確認ダイアログ
- 進捗表示
- キャンセル

### Phase 3: cfilerらしい操作

- インクリメンタルフィルター
- ファイル名検索（`*` / `?` のワイルドカード対応）
- 履歴
- ジャンプリスト
- ブックマーク
- ソート
- フィルタ
- ペイン幅変更
- ステータス表示

### Phase 4: CLIと連携基盤

- CLIからのファイル一覧、コピー、移動、削除、検索
- JSON出力
- `--dry-run`
- `--yes`
- 終了コード
- GUIとCLIで共通のサービスを利用
- assist-chatからサブプロセスとして呼び出せる構成

### Phase 5: Python拡張

- Pythonプラグインの読み込み
- コマンド登録
- 外部キー設定との連携
- メニュー追加
- 通知とログ出力
- ファイル操作API
- ペイン情報・選択項目へのアクセス

### Phase 6: 高度な機能

- テキスト・バイナリ・画像ビューア
- grep
- ファイル比較
- ディレクトリ比較
- アーカイブ作成・展開
- 一括リネーム
- コマンドラインランチャー

## Windows版exeのビルド

GitHub Actionsの`Build Windows executable`ワークフローでWindows版exeをビルドできます。

- `main`ブランチへのpushで自動実行
- Actions画面の`Run workflow`から手動実行も可能
- 完了後、Artifactの`ore-filer-windows`から`ore-filer.exe`を取得

## 自動更新の設計方針（将来実装）

現在は未実装。実装時は、`custom_cfiler`の仕組みを参考にしつつ、次の方針にする。

- **配布元**: GitHub ActionsのArtifactではなく、GitHub Releaseにインストーラーを添付する。Artifactは保存期間や取得方法が安定した一般配布向けではない
- **リリース形式**: `v1.2.3`のようなタグを起点にActionsでビルドし、`ore-filer-installer.exe`とSHA-256チェックサムをReleaseへ登録する
- **確認先**: GitHub Releases APIの`/releases/latest`をHTTPSで取得する。ダウンロードURLは許可したリポジトリのRelease assetに限定する
- **確認タイミング**: 起動時にバックグラウンドで確認する。最終確認日時を`%APPDATA%\ore-filer`へ保存し、標準は1日1回とする。手動確認も用意する
- **バージョン比較**: 文字列比較は使わず、`packaging.version.Version`でSemVerを比較する。現在バージョンは`pyproject.toml`など一カ所を正とする
- **UI**: 新バージョン、変更概要、更新サイズを表示し、ユーザーの明示的な承認後にダウンロードする。サイレント更新はしない
- **検証**: ダウンロード後にSHA-256を検証し、可能ならWindowsのコード署名も検証する。不一致、タイムアウト、壊れたレスポンスの場合はインストールしない
- **インストール**: 実行中のexeは直接上書きせず、NSISやInno Setupのインストーラーを一時フォルダーから起動してアプリを終了する。設定やユーザーデータは保持する
- **失敗時の動作**: ネットワーク障害や更新キャンセルでアプリの起動を妨げない。失敗理由はログへ記録し、次回の確認を可能にする
- **テスト対象**: 最新版、更新あり、無効なバージョン、API障害、タイムアウト、チェックサム不一致、キャンセル、インストール後の再起動を検証する

## cfiler準拠の標準キー

標準設定はcfilerの「デフォルト - 106キーボード」を基準にする。

| キー | 操作 |
|---|---|
| `Up` / `Down` | カーソル移動 |
| `PageUp` / `PageDown` | ページ単位の移動 |
| `gg` | リストの先頭へ移動 |
| `Shift+G` | リストの末尾へ移動 |
| `Left` / `Right` | ペイン切り替え、または親ディレクトリ |
| `Tab` | アクティブペイン切り替え |
| `Backspace` | 親ディレクトリへ移動 |
| `Yen` | ホームディレクトリ（`~`）へ移動 |
| `Return` | フォルダーへ移動、またはアーカイブを開く |
| `Ctrl+Return` | 関連付け実行 |
| `Escape` | 検索結果・フィルターを解除、処理の中断 |
| `Shift+Escape` | バックグラウンド処理の中断 |
| `Space` | 選択して下へ移動 |
| `Shift+Space` | 選択して上へ移動 |
| `Ctrl+Space` | 範囲選択 |
| `A` / `Home` | ファイルを全選択 |
| `Shift+A` / `Shift+Home` | ファイルとディレクトリを全選択 |
| `End` | 選択解除 |
| `Shift+End` | ファイル一覧を更新 |
| `C` | 選択項目をコピー |
| `Shift+C` | コピー先を入力してコピー |
| `M` | 選択あり: 移動、選択なし: ディレクトリ作成 |
| `K` | 完全削除 |
| `Shift+K` | ごみ箱を使用して削除 |
| `R` | リネーム |
| `Shift+R` | 一括リネーム |
| `F` | インクリメンタルフィルター |
| `Shift+F` | ファイル名検索（`*` / `?` 対応、結果一覧を表示） |
| `G` | ファイル内容検索（実装予定） |
| `I` | ファイル情報 |
| `H` | 履歴 |
| `J` | ジャンプリスト |
| `Shift+J` | パス入力で移動 |
| `Ctrl+J` | 検索結果へ移動 |
| `D` | ドライブ選択 |
| `O` | 反対側ペインと同じ場所へ移動 |
| `Shift+O` | 反対側ペインを同じ場所へ移動 |
| `S` | ソート方法を選択 |
| `:` | フィルタを選択 |
| `Backslash` | コンテキストメニュー |
| `B` | ローカルブックマーク |
| `Shift+B` | 全ブックマーク |
| `Ctrl+B` | ブックマーク追加・削除 |
| `L` | ビューア起動 |
| `P` | アーカイブ作成 |
| `U` | アーカイブ展開 |
| `Q` | アプリケーション終了 |
| `Z` | 設定メニュー |

### Pキーのアーカイブ作成

- アクティブペインで選択中の項目を対象にする。選択がない場合はカーソル位置の項目を対象にする
- 保存先は反対側ペインの現在のフォルダー
- ファイル名と任意のパスワードを入力し、ファイル名の拡張子で形式を選択する
- 対応形式は `.zip`、`.jar`、`.apk`、`.7z`、`.tar`、`.tar.gz`、`.tgz`、`.tar.bz2`、`.tbz2`
- パスワードはzip/jar/apkまたは7zで設定できる。tar系でパスワードを入力した場合は作成できない
- 同名ファイルがある場合は上書き確認を表示する
- 作成処理はバックグラウンドで実行し、完了後に左右のペインを更新する

### Uキーのアーカイブ展開

- アクティブペインで選択中のアーカイブを対象にする。選択がない場合はカーソル位置のアーカイブを対象にする
- 展開先は反対側ペインの現在のフォルダー
- 実行前に確認ダイアログを表示する
- 対応形式は `.zip`、`.jar`、`.apk`、`.7z`、`.tar`、`.tar.gz`、`.tgz`、`.tar.bz2`、`.tbz2`
- 複数選択時は、すべて同じ展開先へ順番に展開する
- 展開処理はバックグラウンドで実行し、完了後に左右のペインを更新する
- LZH/RARは未対応

### アーカイブ内の閲覧とコピー

- 対応アーカイブにカーソルを合わせて`Return`を押すと、展開せずに内容をペインへ表示する
- アーカイブ内のフォルダーは通常のフォルダーと同じように`Return`で移動できる
- `Backspace`またはペイン端の`Left`/`Right`で親階層へ戻り、アーカイブ直下からは元のフォルダーへ戻る
- アーカイブ内のファイルやフォルダーを選択して`C`を押すと、反対側の通常フォルダーへコピーする
- パスワード付きzip/7zは、開くときにパスワードを入力する
- アーカイブをコピー先に指定することはできない

キー割り当ては後述のTOMLファイルから変更できる。

## 外部キー設定

ユーザー設定は次の場所に保存する。

```text
%APPDATA%\\ore-filer\\keymap.toml
```

開発時の標準設定は`config/keymap.toml`に置く。初回起動時にユーザー設定がなければ標準設定を使用する。

```toml
[keys]
cursor_up = "Up"
cursor_down = "Down"
switch_pane = "Tab"
parent_directory = "Backspace"
copy = "C"
move = "M"
delete_permanent = "K"
delete_recycle_bin = "Shift+K"
rename = "R"
incremental_filter = "F"
file_name_search = "Shift+F"
history = "H"
jump_list = "J"
select_drive = "D"
refresh = "Shift+End"
quit = "Q"
```

キーからPython関数へ直接接続せず、操作名を経由する。

```text
キー入力 -> 操作名 -> コマンド -> サービス
```

設定の読み込み時には次を検証する。

- 存在しない操作名
- 不正なキー表記
- 同じキーの重複
- 必須操作の欠落
- 壊れたTOML

設定が壊れている場合はエラーを表示し、標準設定で起動する。将来的には`Z`キーによる設定再読み込みに対応する。

## CLI

GUIとCLIは同じサービス層を利用する。CLIはGUIのペイン状態に依存せず、パスを明示的に受け取る。

```powershell
ore-filer list C:\Users\SatoshiｰOotomo\Downloads
ore-filer copy "C:\work\a.txt" "D:\backup"
ore-filer move "C:\work\a.txt" "D:\archive"
ore-filer rename "C:\work\a.txt" "b.txt"
ore-filer mkdir "C:\work\new-folder"
ore-filer delete "C:\work\a.txt" --recycle-bin
ore-filer search "C:\work" "*.py"
```

assist-chat連携用にJSON出力を提供する。

```powershell
ore-filer copy "C:\work\a.txt" "D:\backup" --json
```

```json
{
  "success": true,
  "operation": "copy",
  "sources": ["C:\\work\\a.txt"],
  "destination": "D:\\backup",
  "results": [
    {
      "source": "C:\\work\\a.txt",
      "target": "D:\\backup\\a.txt",
      "status": "completed"
    }
  ]
}
```

CLIの方針:

- `--json`: 機械可読な結果を標準出力へ出す
- `--dry-run`: 実際には変更せず実行内容だけ確認する
- `--yes`: 削除や上書きを明示的に許可する
- 成功・失敗を終了コードで通知する
- 通常の結果は標準出力、エラーは標準エラー出力へ出す
- 削除・上書きは暗黙に実行しない

## Pythonプラグイン

プラグインは次の場所から読み込む。

```text
%APPDATA%\\ore-filer\\plugins\\
```

プラグインは安定した公開APIを利用し、Qt内部のウィジェットを直接操作しない。

```python
from ore_filer.api import Command


class SampleCommand(Command):
    command_id = "sample.show_message"
    label = "サンプルメッセージ"

    def execute(self, context):
        context.notify("プラグインから実行しました")


def register(app):
    app.commands.register(SampleCommand())
```

公開APIの候補:

- アクティブペインの取得
- 左右ペインのパス取得
- 選択ファイルの取得
- ファイル一覧の更新
- コピー・移動・削除
- ブックマークの追加・取得
- 通知・ログ出力
- コマンド登録
- メニュー追加
- キー割り当て追加
- 外部コマンド起動

プラグインは任意のPythonコードを実行できるため、初期版ではユーザーが明示的に配置したディレクトリのプラグインだけを読み込む。

## GUI、CLI、プラグインの関係

```text
cfiler互換キー
       |
       v
    コマンド層 <----- CLI
       |
       v
  共通サービス層 <--- Pythonプラグイン
       |
       v
  ファイルシステム
```

GUI、CLI、プラグインがそれぞれ独自にコピーや削除を実装しないことが重要。ファイル操作の安全確認、エラー処理、進捗通知は共通サービス層に集約する。

## 当面の完成目標

最初の実装では、次の範囲を完成させる。

- 左右2ペイン
- cfiler準拠のカーソル移動
- `Tab`によるペイン切り替え
- フォルダ移動
- ファイル選択
- 外部TOMLによるキー設定
- `C`によるコピー
- `M`による移動
- `R`によるリネーム
- `Shift+K`によるごみ箱削除
- `Shift+End`による再読み込み
- 操作確認とエラー表示

その後、共通サービス層をCLIから呼び出せるようにし、assist-chat連携の基盤を作る。
