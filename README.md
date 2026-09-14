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
│   ├── main_window.py
│   ├── pane.py
│   ├── file_model.py
│   ├── keymap.py
│   ├── commands.py
│   ├── file_operations.py
│   ├── dialogs.py
│   ├── settings.py
│   ├── services/
│   │   ├── file_service.py
│   │   ├── directory_service.py
│   │   └── search_service.py
│   ├── api/
│   │   ├── app.py
│   │   ├── command.py
│   │   ├── context.py
│   │   └── filesystem.py
│   ├── gui/
│   │   └── main_window.py
│   └── cli/
│       └── main.py
├── config/
│   └── keymap.toml
└── plugins/
    └── sample_plugin/
        ├── plugin.py
        └── README.md
```

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

- インクリメンタルサーチ
- ファイル名検索
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

## cfiler準拠の標準キー

標準設定はcfilerの「デフォルト - 106キーボード」を基準にする。

| キー | 操作 |
|---|---|
| `Up` / `Down` | カーソル移動 |
| `PageUp` / `PageDown` | ページ単位の移動 |
| `Ctrl+PageUp` / `Ctrl+PageDown` | リストの先頭・末尾 |
| `Left` / `Right` | ペイン切り替え、または親ディレクトリ |
| `Tab` | アクティブペイン切り替え |
| `Backspace` | 親ディレクトリへ移動 |
| `Yen` | ルートディレクトリへ移動 |
| `Return` | 開く、関連付け実行 |
| `Escape` | 処理の中断 |
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
| `Shift+M` | 移動先を入力して移動 |
| `K` | 完全削除 |
| `Shift+K` | ごみ箱を使用して削除 |
| `R` | リネーム |
| `Shift+R` | 一括リネーム |
| `F` | インクリメンタルサーチ |
| `Shift+F` | ファイル名検索 |
| `Shift+G` | ファイル内容検索 |
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
incremental_search = "F"
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
