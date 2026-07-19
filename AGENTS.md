# AGENTS.md

## Windows UTF-8

- ファイルの読み書きは必ずUTF-8で行う。
- PowerShell表示だけで文字化け判断をしない。
- 日本語や非ASCIIを確認する時は、`Get-Content -Encoding UTF8` またはPythonの `Path.read_text(encoding="utf-8")` を使う。

## 介入API

- 介入APIは監視アプリ機能の一部。起動cmdが親として一緒に起動する。
- 起動cmd上のGUIプロセス終了後に介入APIも停止する。
- GUI起動: `J:\utility\Niconico\niconico-watch-app\scripts\start_gui.cmd`
- API単体起動: `J:\utility\Niconico\niconico-watch-app\scripts\start_intervention_api.cmd`
- URL: `http://127.0.0.1:8794`
- DB操作は直接SQLiteを書かず、原則このAPIを通す。
- 疎通確認: `GET /health`
- スペシャルユーザー登録/更新: `POST /api/special-users`
- コメビュからの検出登録: `POST /api/special-users/detected`

## コメビュコメント変換

- 現在のCLI入口: `tools/import_simple_comment_viewer_comments.py`
- 再利用対象は、コメビュの `normalized_events` にある `chat` を監視アプリの `archive_comments` 形式へ写すスキーマ変換である。
- 別LVへ全件投入した過去の使い方は特殊な一回ジョブであり、毎回同じ投入方法を使う前提にしない。
- `--source-lv` と `--target-lv` は必須。実行前に必ず同じ指定で `--dry-run` を通す。
- `--append` なしは既存対象コメントをバックアップして置換する。`--append` の反復実行は重複を作るため禁止する。
- 変換処理は他の連携経路からも再利用できる部品として扱い、CLI固有のDB置換・ランキング再構築・ファイル出力とは分けて考える。
- 常駐同期やスペシャルユーザー検出APIとは別物として管理し、自動起動しない。

## Git

- コミットを指示されたら、原則として必ず `git add .` でステージングする。
- コミットメッセージは必ず日本語で書く。
