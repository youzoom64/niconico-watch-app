# legacy_archiver step arguments

旧 `niconico-archiver` の `pipeline.py` / `processors/step*.py` が要求する入力。

## Pipeline entry

`legacy_archiver/pipeline.py` のCLI引数:

```text
python pipeline.py <platform> <account_id> <platform_directory> <ncv_directory> <lv_value>
```

内部で作る `pipeline_data`:

```python
{
    "platform": platform,
    "account_id": account_id,
    "platform_directory": platform_directory,
    "ncv_directory": ncv_directory,
    "lv_value": lv_value,
    "user_name": account_id,
    "config": config,
    "start_time": datetime.now(),
    "results": {},
}
```

`config` は旧実装では `config/users/{account_id}.json` から読む。

## Directory contract

各stepはほぼ共通して次の場所を使う。

```text
account_dir   = find_account_directory(platform_directory, account_id)
broadcast_dir = account_dir / lv_value
```

つまり旧実装の期待パスは基本的に:

```text
{platform_directory}\{account_id または account_id_表示名}\{lv_value}
```

現在の新アプリの保存先:

```text
<target_root>\platform\niconico\{account_id}\broadcast\{lv_value}
```

とは `bloadcast` 階層が違う。

`platform` 引数自体は旧step内では実質使われていない。効いているのは `platform_directory`。

新アプリ側では次の形で渡す。

```text
platform = niconico
platform_directory = <target_root>\platform\niconico
account_id = 51610839 など
```

コピー済み `legacy_archiver/utils.py` の `find_account_directory()` は、新アプリ互換として
`{platform_directory}\{account_id}\bloadcast` が存在する場合そこを返すようにした。

これで旧stepから見た:

```text
account_dir / lv_value
```

は実際には:

```text
<target_root>\platform\niconico\{account_id}\broadcast\{lv_value}
```

になる。

検証済み:

```text
lv350790685
step12_html_generator -> lv350790685_おは.html 生成成功
step13_index_generator -> bloadcast/index.html 生成成功
step14_modern_list_generator -> bloadcast/modern_list.html 生成成功
```

## Step inputs

| step | pipeline_data keys | config keys | 主な入力ファイル | 主な出力 |
|---|---|---|---|---|
| step01_data_collector | `account_id`, `config`, `lv_value`, `ncv_directory`, `platform_directory` | `display_name` | NCV XML、動画ファイル、放送ページHTML | `{lv}_data.json`, `{lv}.html` |
| step02_audio_transcriber | `account_id`, `config`, `lv_value`, `platform_directory` | `audio_settings` | account_dir直下の動画、`{lv}_data.json` | `{lv}_transcript.json`, `{lv}_full_audio.mp3`, chunk mp3 |
| step03_emotion_scorer | `account_id`, `lv_value`, `platform_directory` | なし | `{lv}_data.json`, `{lv}_transcript.json` | data/transcriptへ感情情報追記 |
| step04_word_analyzer | `account_id`, `lv_value`, `platform_directory` | なし | `{lv}_data.json`, `{lv}_transcript.json` | dataへ単語ランキング追記 |
| step05_summarizer | `account_id`, `config`, `lv_value`, `platform_directory` | `ai_prompts`, `api_settings` | `{lv}_data.json`, `{lv}_transcript.json` | `{lv}_summary.txt`, dataへ要約追記 |
| step06_music_generator | `account_id`, `config`, `lv_value`, `platform_directory` | `ai_features`, `api_settings`, `music_settings` | `{lv}_data.json` | 音楽生成結果をdataへ追記 |
| step07_image_generator | `account_id`, `config`, `lv_value`, `platform_directory` | `ai_features`, `ai_prompts`, `api_settings` | `{lv}_data.json` | 画像生成結果をdataへ追記 |
| step08_conversation_generator | `account_id`, `config`, `lv_value`, `platform_directory` | `ai_features`, `ai_prompts`, `api_settings` | `{lv}_data.json` | intro/outro会話をdataへ追記 |
| step09_screenshot_generator | `account_id`, `config`, `lv_value`, `platform_directory` | `display_features` | account_dir直下の動画、`{lv}_data.json` | `screenshot/{lv}/...jpg` |
| step10_comment_processor | `account_id`, `lv_value`, `platform_directory` | なし | `{lv}_data.json` | `{lv}_comments.json`, `{lv}_comment_ranking.json` |
| step11_special_user_html_generator | `account_id`, `config`, `lv_value`, `platform_directory` | `api_settings`, `special_users`, `special_users_config`, `users` など | `{lv}_data.json`, `{lv}_comments.json`, template | `special_user_{user_id}/{user_id}_{lv}_detail.html` |
| step12_html_generator | `account_id`, `config`, `lv_value`, `platform_directory` | `ai_prompts` | `{lv}_data.json`, `{lv}_transcript.json`, `{lv}_comments.json`, `{lv}_comment_ranking.json` | `{lv}_{title}.html`, dataへ`html_file_path`追記 |
| step13_index_generator | `account_id`, `config`, `platform_directory` | `tags` | 各放送dirの `{lv}_data.json`, `{lv}_transcript.json` | `index.html`, `tags/tag_*.html` |
| step14_modern_list_generator | `account_id`, `config`, `platform_directory` | `tags` | 各放送dirの `{lv}_data.json`, `{lv}_transcript.json` | モダン一覧HTML |

## Important mismatch

旧step02/step09は「動画が `account_dir` 直下にある」前提で探す。

新アプリ側は録画を `bloadcast/{lv}` に集約する方針なので、旧step02/step09をそのまま使うより、新アプリ側で以下を先に作ってから後段stepへ渡す方が安定する。

- `{lv}_data.json`
- `{lv}_comments.json`
- `{lv}_comment_ranking.json`
- `{lv}_transcript.json`
- `{lv}_full_audio.mp3` 相当
- 必要なら `screenshot/{lv}/...jpg`

## SlNicoLiveRec recording note

SlNicoLiveRec の録画中ファイルは、途中で `0 bytes` に見えることがある。

これは「録画できていない」とは限らない。SlNicoLiveRec は録画データを一時的にメモリ側へ持ち、プロセス終了時や録画終了時にファイルへまとめて書き出す挙動をすることがある。

そのため、録画中の `.ts` ファイルについては次の前提で扱う。

- 録画中に `0 bytes` でも、即座に失敗扱いしない。
- `LastWriteTime` やファイルサイズだけで、録画の実欠落時間を判断しない。
- プロセス再起動時刻と、実際に映像データがファイルへ反映される時刻は一致しないことがある。
- gap 計算は「親プロセスが子プロセスを起動した時刻」だけでは不十分な場合がある。
- 最終判断は録画プロセス終了後、ファイルサイズが確定してから行う。

例:

```text
lv350802767_2026_0621_224955_...ts  101,318,656 bytes
lv350802767_2026_0621_232001_...ts            0 bytes  # 録画中なら異常確定ではない
```

この場合、2本目が `0 bytes` でも「再起動後の録画が完全に失敗した」と即断しない。
SlNicoLiveRec が終了したタイミングでファイルへ一気に反映される可能性がある。
