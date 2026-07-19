# ニコニコ関連API一覧

確認日: 2026-06-21

この文書は `niconico-watch-app` で使う候補のAPI/ページを整理したもの。  
旧NCV XMLの代替に使う情報源もここにまとめる。

## 1. 放送者の放送履歴API

### Endpoint

```text
GET https://live.nicovideo.jp/front/api/v2/user-broadcast-history
```

### 用途

- 放送者IDから放送履歴を取得する
- 現在放送中かどうかを確認する
- 終了済み放送のメタ情報を取得する
- NCV XMLの以下を代替する
  - `ElapsedTime`
  - `WatchCount`
  - `CommentCount`
  - `DefaultCommunity`
  - `LiveTitle`
  - `StartTime`
  - `EndTime`
  - `OwnerId`
  - `OwnerName`

### Parameters

```text
providerId=130224542
providerType=user
isIncludeNonPublic=false
offset=0
limit=10
withTotalCount=true
```

### Headers

```text
User-Agent: Mozilla/5.0
X-Frontend-Id: 9
X-Frontend-Version: 0
```

### 取得できる主な値

```text
id.value
program.title
program.description
program.provider
program.schedule.status
program.schedule.openTime.seconds
program.schedule.beginTime.seconds
program.schedule.scheduledEndTime.seconds
program.schedule.endTime.seconds
program.schedule.vposBaseTime.seconds
programProvider.type
programProvider.name
programProvider.profileUrl
programProvider.programProviderId.value
socialGroup.socialGroupId
socialGroup.type
socialGroup.name
statistics.viewers.value
statistics.comments.value
thumbnail.screenshot
thumbnail.listing
```

### 旧NCV XMLとの対応

```text
LiveTitle
→ program.title

StartTime
→ program.schedule.beginTime.seconds

OpenTime
→ program.schedule.openTime.seconds

EndTime
→ program.schedule.endTime.seconds

ElapsedTime
→ endTime.seconds - beginTime.seconds

WatchCount
→ statistics.viewers.value

CommentCount
→ statistics.comments.value

OwnerId
→ programProvider.programProviderId.value

OwnerName / Broadcaster
→ programProvider.name

DefaultCommunity
→ socialGroup.socialGroupId

CommunityName
→ 旧NCV XMLの項目。現行ニコニコ仕様では使わない。
```

### 実測例

対象:

```text
lv350787520
providerId=130224542
```

結果:

```text
title: たぶん1.5時間くらいニコゲー！【広告イベ参加中】
status: ENDED
beginTime: 1781879845
endTime: 1781891422
elapsed: 11577秒
watch_count: 186
comment_count: 416
programProvider.name: りる果
programProvider.programProviderId.value: 130224542
socialGroup.socialGroupId: co0
socialGroup.name: 削除されたコミュニティ
```

### 注意

- LVだけで直接引くAPIではない。
- `providerId` が必要。
- まず放送ページHTMLやDBから放送者IDを確定してから、このAPIで対象LVを探す。
- チャンネルの場合は `providerType` や `providerId` の扱いを別途確認する。

## 2. 放送ページHTML embedded-data

### URL

```text
GET https://live.nicovideo.jp/watch/{lv}
```

### 用途

- LVだけから放送メタを取得する
- `script#embedded-data` の `data-props` に埋め込まれたJSONを読む
- `user-broadcast-history` を叩くための `programProviderId` を取る

### 取得方法

HTML内:

```html
<script id="embedded-data" data-props="...">
```

`data-props` はHTMLエスケープされたJSON。

### 取得できる主な値

```text
program.nicoliveProgramId
program.providerType
program.visualProviderType
program.title
program.supplier.name
program.supplier.programProviderId
program.supplier.pageUrl
program.openTime
program.beginTime
program.vposBaseTime
program.endTime
program.scheduledEndTime
program.status
program.statistics.watchCount
program.statistics.commentCount
program.socialGroup.id
program.socialGroup.name
program.socialGroup.type
```

### 旧NCV XMLとの対応

```text
LiveTitle
→ program.title

StartTime
→ program.beginTime

OpenTime
→ program.openTime

EndTime
→ program.endTime

ElapsedTime
→ program.endTime - program.beginTime

WatchCount
→ program.statistics.watchCount

CommentCount
→ program.statistics.commentCount

OwnerId
→ program.supplier.programProviderId

OwnerName / Broadcaster
→ program.supplier.name

DefaultCommunity
→ program.socialGroup.id

CommunityName
→ 旧NCV XMLの項目。現行ニコニコ仕様では使わない。
```

### 実測例

対象:

```text
https://live.nicovideo.jp/watch/lv350787520
```

結果:

```text
program.supplier.name: りる果
program.supplier.programProviderId: 130224542
program.openTime: 1781879845
program.beginTime: 1781879845
program.endTime: 1781891422
program.status: ENDED
program.statistics.watchCount: 186
program.statistics.commentCount: 416
```

### 注意

- HTML構造変更に弱い。
- ただしLVだけから取得できるので、APIに渡す放送者IDの特定に使える。

## 3. 最近の放送ページ

### URL

```text
GET https://live.nicovideo.jp/recent?tab=common
```

### 用途

- トラッカーの基本入口
- SeleniumでDOMから放送中一覧を取得する
- `もっと見る` を押して表示件数を増やしてからDOMを読む

### 取得している主な値

```text
lv
title
broadcaster_id
broadcaster_name
watch_url
elapsed_minutes
watch_count
comment_count
status
```

### 注意

- APIではなくページDOM取得。
- 現状のトラッカーはここから取得して `broadcasts` テーブルへ保存している。
- DOM変更に弱い。

## 4. ユーザーの生放送一覧ページ

### URL

```text
GET https://www.nicovideo.jp/user/{user_id}/live_programs
```

### 用途

- 特定ユーザーが現在ON_AIRか確認する
- DOM内の `data-status-type="ON_AIR"` を探す

### 注意

- APIではなくページDOM取得。
- 現在はAPIの `user-broadcast-history` のほうが軽くて安定候補。

## 5. チャンネルページ

### URL

```text
GET https://ch.nicovideo.jp/{channel_slug}
```

### 用途

- チャンネル放送のON_AIR確認
- `data-live_status="onair"` と `data-live_id` を拾う

### DOM例

```html
<span class="timeshift_button lv350576857" data-live_id="350576857" data-live_status="onair">
```

### 注意

- API代替は未確定。
- チャンネル系は `user-broadcast-history` と同じAPIで取れるか追加確認が必要。

## 6. コメント取得

### 現在の実装

```text
ndgr_client.NDGRClient
```

### 用途

- 生放送コメントのリアルタイム取得
- 過去ログ一括取得の入口としても利用

### DB保存先

```text
archive_comments
archive_comment_ranking
```

### 旧NCV XMLとの対応

```text
NCV XML chat
→ archive_comments

comments.json
→ archive_comments から生成可能

comment_ranking.json
→ archive_comment_ranking または archive_comments 集計で生成可能
```

## 7. 投稿

### 現在の実装

```text
nicolive_post.py
```

### 入口

```text
GET https://live.nicovideo.jp/watch/{live_id}
```

### 用途

- Cookie / `user_session` を使ったコメント投稿

### 注意

- 詳細APIはまだ確定整理していない。
- 実投稿は成功確認済みだが、内部経路は追加調査が必要。

## 8. 試したが使えなかった候補

対象:

```text
lv350787520
```

### 404だったもの

```text
GET https://live.nicovideo.jp/front/api/v1/programs/lv350787520
GET https://live.nicovideo.jp/front/api/v2/programs/lv350787520
GET https://live.nicovideo.jp/api/watch/lv350787520
GET https://live.nicovideo.jp/api/getplayerstatus/lv350787520
GET https://live.nicovideo.jp/watch/lv350787520/programinfo
```

### 結論

- LV直指定の公開APIは現時点では見つかっていない。
- LVだけから始める場合は放送ページHTMLの `embedded-data` を読むのが現実的。
- 放送者IDがある場合は `front/api/v2/user-broadcast-history` が使える。

## 9. 現時点の推奨取得順

### LVだけある場合

```text
1. https://live.nicovideo.jp/watch/{lv} を取得
2. script#embedded-data の data-props を読む
3. supplier/programProviderId と providerType を取得
4. 必要なら user-broadcast-history で履歴情報を補完
```

### 放送者IDがある場合

```text
1. front/api/v2/user-broadcast-history を取得
2. programsList から対象LVを探す
3. schedule/statistics/socialGroup/programProvider を読む
```

### 旧NCV XML代替

```text
放送メタ:
  embedded-data または user-broadcast-history

コメント:
  NDGR → archive_comments

ランキング:
  archive_comment_ranking または archive_comments 集計

文字起こし:
  archive_transcript_segments
```
