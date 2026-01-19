# Bitget版 設計書（ETHUSDT SPOT + USDT-PERP）

対象戦略: Market Making（PERP） + 板不均衡（OBI） + Fundingフィルタ/バイアス + SPOTヘッジ（デルタ中立）
実装方針: まずは MVP（最小で安全に動く骨格） を固め、あとから精緻化（Basis・最適化・多銘柄化）する。
取引所: Bitget V2（REST + WebSocket）
実装ライブラリ: pybotters（DataStoreで books/orders/fill/positions を受ける）

## 0. ゴールと非ゴール

### ゴール（MVP）

- PERPで指値（post_only）を両建てで提示し、約定（fill）が来たら SPOTで即時（IOC）ヘッジして デルタ中立（perp_pos + spot_pos ≒ 0） を維持する。
- Fundingが「獲りに行く方向」のときだけ稼働（例：Fundingが+なら PERPショートを持ちたい、など）。
- perp fill → spot hedge が 1回でも実運用（DRY_RUN=0）で確認できる。
- 安全に止まる（制約不明・WS死・未ヘッジ露出過大・注文拒否多発→fail-closed）

### 非ゴール（MVPではやらない）

- 多取引所裁定、レイテンシ競争、板更新をマイクロ秒で戦うHFT
- 完全なPnL会計（税/複数通貨/借入/利息等）
- Basis（perp-spot）の高度モデル（後で追加）

## 1. システム全体像（コンポーネントと責務）

### 1.1 コンポーネント

#### BitgetMarketData（WS Public）

- books5 を購読して SPOT/PERP の板（上位5段）を受信
- BBO（best bid/ask）と OBI（Order Book Imbalance）を算出
- WSは wss://ws.bitget.com/v2/ws/public を利用

#### BitgetPrivateData（WS Private）

- orders, fill, positions を購読して、自分の注文状態/約定/ポジション変化を受信
- WSは wss://ws.bitget.com/v2/ws/private を利用
- 取引所仕様として、接続維持の ping/pong（例：30秒ごとに ping、2分無pingで切断等）を守る

#### BitgetREST（発注・キャンセル・Funding取得・制約取得）

- PERP：/api/v2/mix/order/place-order, /api/v2/mix/order/cancel-order
- SPOT：/api/v2/spot/trade/place-order, /api/v2/spot/trade/cancel-order
- Funding：PERPの funding rate を取得（REST）
- 制約：精度（price/qty）・min notional 等を起動時にロード

#### Strategy（MM + OBI + Funding）

入力：BBO/板、OBI、funding、ポジション、リスク状態  
出力：
- QUOTE（PERPの bid/ask 指値を置く/更新/キャンセル）
- HEDGE（PERP fillを受けて SPOT IOC ヘッジを出す）
- RISK（flatten/cooldown）

#### OMS（注文管理）

- clientOid 生成規約、注文レジストリ、キャンセル/置換、冪等（exec/trade 重複）
- fillを受けて perp→spotのヘッジを起動する“橋渡し”を担当
- 並列事故（同一symbolのclose二重発火）をロックで防止（あなたが入れた対策の思想を標準化）

#### Risk/Guards（fail-closed）

- 制約不明なら出さない
- 未ヘッジ露出（unhedged notional/time）超過で強制縮退
- WSが死んだら全キャンセル＆停止
- 注文拒否が続いたら停止

#### Logger（JSONL）

- order_new/order_skip/fill/tick/risk/state/constraints_loaded を構造化ログで吐く
- 後から「何が発火したか」をログだけで確定（intent/source/mode/reason/leg/cycle_id）

## 2. Bitget固有仕様（この設計のキモ）

### 2.1 ドメイン

- REST: https://api.bitget.com
- WS Public: wss://ws.bitget.com/v2/ws/public
- WS Private: wss://ws.bitget.com/v2/ws/private

### 2.2 WS購読（Public）

books5（5段板。毎回 snapshot が飛ぶ）

例（PERP: USDT-FUTURES）

```json
{
  "op": "subscribe",
  "args": [
    { "instType": "USDT-FUTURES", "channel": "books5", "instId": "ETHUSDT" }
  ]
}
```

SPOTも同様に instType: "SPOT" を指定して books5 購読（spot側のDepth Channel仕様）

### 2.3 WS購読（Private）

PERP（USDT-FUTURES）

- fill：約定が発生したとき push
- orders：注文作成/約定/キャンセルで push
- positions：ポジション変化で push

SPOT（SPOT）

- fill：約定 push（orderId/tradeId/priceAvg/size/feeDetail/cTime/uTime 等）
- orders：注文作成/約定/キャンセル/変更で push

### 2.4 ポジションモード（超重要）

PERP注文はアカウントの posMode に強く依存。

GET /api/v2/mix/account/account のレスポンスに posMode があり、  
one_way_mode / hedge_mode が返る

Place Order の仕様：

- one-way-mode では tradeSide を無視する
- hedge-mode では tradeSide が必要（open/close）

code=40774 などの拒否は、posMode不一致の典型。  
このときは「モードを揃える」か「注文パラメータをモードに合わせる」が必須。

MVPの推奨: one_way_mode に寄せる（実装が単純・posSide管理不要）。

ただし、productType内にポジション/注文があるとモード変更が失敗することがあるので、起動時に fail-closed にする。

## 3. pybotters採用設計（DataStore中心）

### 3.1 WS受信は DataStore に集約

pybottersは、WSの hdlr_json に store.onmessage を渡すことで取引所固有DataStoreに格納できる。  
また、板系DataStoreは sorted() で bids/asks を辞書形式で取り出せる。  
変更ストリームは watch() でイベント駆動にできる。

### 3.2 ループモデル（推奨）

WS受信は pybotters が DataStore へ格納。  
戦略は

- (A) while True: await store.book.wait(); compute(); maybe_quote() の “更新駆動”
- (B) asyncio.sleep(TICK_SEC) の “固定周期”

どちらでも良いが、MVPは (B) のほうがバグりにくい。  
fill/positions は watch() でイベント駆動にして ヘッジ遅延を最小化。

## 4. データモデル（アプリ内の正規化）

### 4.1 正規化したい最小型

- BBO：bid/ask/size/ts
- ExecutionEvent：instType(symbol), side, price, size, orderId, tradeId, clientOid(あれば), fee, ts
- OrderIntent：QUOTE | HEDGE | UNWIND | FLATTEN
- Leg：perp_bid | perp_ask | spot_ioc | perp_unwind | spot_unwind ...
- State：後述（QUOTING / HEDGING / COOLDOWN / HALTED）

### 4.2 SPOT fillは clientOid が無い前提で設計

SPOT fill の例データは orderId/tradeId が中心（clientOidが出るとは限らない）。  
→ **OMSは “spot注文発注時の orderId を保存”**し、fillの orderId で引けるようにする。  
（PERP fill は clientOid が含まれるので、こちらは clientOid を第一キーにできる）

## 5. 戦略仕様（MM + OBI + Funding）

### 5.1 入力

- PERP books5（bids/asks上位5段）
- SPOT books5（BBOだけ使ってもOK）
- funding_rate（PERP）
- positions（PERP, SPOT）
- risk flags（stale, reject streak, unhedged exposure…）

### 5.2 OBI（板不均衡）の定義（例）

上位N段（MVPは N=5）を使って：

- bid_liq = Σ bid_size[i]
- ask_liq = Σ ask_size[i]
- obi = (bid_liq - ask_liq) / (bid_liq + ask_liq + eps)

範囲は概ね [-1, +1]。  
+に寄るほど買い圧（bid優勢）。

### 5.3 “作るべき”PERPのクォート（post_only）

基本方針: PERPで maker を狙い、SPOTはヘッジのために taker/IOC を許容。

PERP mid:

mid = (perp_bid + perp_ask)/2

予約価格（reservation price）:

r = mid * (1 + k_obi * obi)

例：obiが+なら rを上げ、買い側を積極化/売り側を消極化

half spread:

h = base_half_spread_bps + inventory_skew_bps + funding_skew_bps

価格:

- bid_px = r * (1 - h)
- ask_px = r * (1 + h)

注文:

- PERP bid: side=buy, orderType=limit, force=post_only
- PERP ask: side=sell, orderType=limit, force=post_only

books5 は snapshot が来るので更新は “価格差が閾値超えたらcancel&replace”

### 5.4 Funding の使い方（MVP）

Fundingは **「稼働フィルタ」＋「クォートの非対称化」**として使う。

フィルタ（例）

- abs(funding_rate) < min_abs_funding → 稼働しない（quotesを外す）

バイアス（例）

Fundingが「ショート有利」なら

- ask をやや aggressive（約定しやすく）
- bid をやや passive

Fundingが逆なら逆方向。

ここは“勝ち筋”の中心。Fundingが薄いのにスプレッド/ヘッジコストが重いと確実に負ける（あなたがbacktestで見た構図）。

## 6. ヘッジ仕様（perp fill → spot IOC）

### 6.1 トリガ

PERPの fill イベント受信（private fill）で発火

### 6.2 ルール

PERP fill の side と “ヘッジ方向”：

- PERPが buy fill（PERPロングが増える） → SPOTを sell して相殺
- PERPが sell fill（PERPショートが増える） → SPOTを buy して相殺

### 6.3 SPOT注文タイプ

MVPは limit + IOC を推奨（理由：market buy は size が quote になるなど罠がある）

SPOTの size は

- Limit と Market-Sell は base
- Market-Buy は quote

という仕様があるため、MVPは IOC limit で統一するのが安全。

### 6.4 SPOT IOC 価格決定（例）

buy hedge:

px = spot_ask * (1 + hedge_slip_bps)

sell hedge:

px = spot_bid * (1 - hedge_slip_bps)

price_precision で丸め、min_trade_usdt を満たさないなら order_skip（fail-closed）

### 6.5 部分約定・未ヘッジ露出

spotが部分約定 or 失敗したら

- remaining_qty を追いかける（slipを増やす）
- max_unhedged_sec を超えたら perp側をunwind（損切りしてでもデルタを消す）

この未ヘッジ制御が「市場中立ボット」の生命線。

## 7. OMS設計（注文・約定・冪等）

### 7.1 注文キーと clientOid 規約

clientOid は intent + leg + cycle_id + short_uuid の形で衝突しないように。  
PERPは fill に clientOid が入るので、clientOid→戦略意図が追跡できる。  
SPOTは fill が orderId中心なので、発注レスの orderId を保存して追跡する。

### 7.2 冪等（重複fill対策）

PERP fill は tradeId があるので、(instType, tradeId) を一意として dedupe。  
SPOT fill も tradeId があるので同様に dedupe。  
さらに orderId も併用し、再接続でスナップショットが重複しても崩れないようにする。

### 7.3 close/flatten の排他

あなたが踏んだ事故（flatten_all と通常CLOSEが並走→二重決済）を一般化して、

- symbol 単位の asyncio.Lock
- closing_symbols セット

で “同時close禁止” を設計上の必須とする。

## 8. リスク設計（fail-closedの具体）

### 8.1 ガード一覧（MVP必須）

- constraints_missing  
  spot/perpの精度・min notional が取れない → 一切出さない（order_skip）
- book_stale  
  books5 が一定時間更新されない → quote全cancel、停止
- funding_stale  
  funding更新が古い → quote停止（薄利戦略なので危険）
- unhedged_exposure  
  abs(unhedged_notional) > max_unhedged_notional or unhedged_sec > max_unhedged_sec  
  → quote停止、unwind/flatten
- reject_streak  
  注文拒否が連続（posMode不一致/制約違反/権限不足/レート制限）  
  → 即停止（人間が見るまでHALT）

### 8.2 posMode起因の拒否対応

起動時に GET /api/v2/mix/account/account で posMode を取得し、想定と違うなら

- 可能なら Change Hold Mode を試す（ただしポジション/注文があると失敗しうる）
- 失敗したら停止（fail-closed）

## 9. Bitget REST パラメータ雛形（この設計の “貼り付け用”）

### 9.1 PERP（USDT-FUTURES）指値 post_only（クォート）

Bitget Place Order の仕様上、one-wayでは tradeSide 無視、hedge-modeでは tradeSide 必須。  
one_way_mode を前提（推奨）

```json
{
  "symbol": "ETHUSDT",
  "productType": "USDT-FUTURES",
  "marginMode": "isolated",
  "marginCoin": "USDT",
  "size": "0.05",
  "price": "2992.9",
  "side": "sell",
  "orderType": "limit",
  "force": "post_only",
  "clientOid": "Q...."
}
```

tradeSide は one-way では無視される（付けても害は少ないが、MVPは統一した方が良い）。

### 9.2 PERP キャンセル（orderId or clientOid）

POST /api/v2/mix/order/cancel-order は orderId か clientOid のどちらかが必須。

```json
{
  "symbol": "ETHUSDT",
  "productType": "USDT-FUTURES",
  "marginCoin": "USDT",
  "clientOid": "Q...."
}
```

### 9.3 SPOT ヘッジ（limit + IOC）

SPOTの size 仕様（market-buyがquote等）があるため IOC limit 推奨。

```json
{
  "symbol": "ETHUSDT",
  "side": "buy",
  "orderType": "limit",
  "force": "ioc",
  "price": "2993.26",
  "size": "0.05",
  "clientOid": "H...."
}
```

### 9.4 SPOT キャンセル

POST /api/v2/spot/trade/cancel-order

```json
{
  "symbol": "ETHUSDT",
  "orderId": "121211212122"
}
```

## 10. ログ設計（あなたの現行JSONLを“設計として固定”）

### 10.1 必須フィールド

- ts（ms）
- event（tick/order_new/order_skip/fill/state/risk/constraints_loaded…）
- intent（quote/hedge/unwind/flatten）
- source（strategy/oms/risk）
- mode（QUOTING/HEDGING/COOLDOWN/HALTED）
- reason（set_quote, constraints_missing, unhedged_breach …）
- leg（perp_bid/perp_ask/spot_ioc/perp_unwind…）
- cycle_id（戦略ステップの相関ID）
- data（API payload）
- res（取引所レスポンス）
- simulated（紙/疑似約定の印）

### 10.2 これがあると最強（推奨）

- exch_order_id（orderId）
- exch_trade_id（tradeId）
- dedupe_key（instType+tradeId）
- pos_snapshot（perp_pos/spot_pos/unhedged_notional）

## 11. 実装順（Bitgetで死ににくい順）

### P0（今すぐ）

- 起動時：constraintsロード（spot/perp）→ constraints_missingなら停止
- 起動時：GET /mix/account/account で posMode を確認（想定と違うなら停止 or 変更試行）
- WS接続：public books5（spot+perp）＋ private fill/orders/positions
- QUOTE: PERPにpost_only両建てを出す（最小サイズ）
- PERP fill を受けたら SPOT IOC hedge（1回でいいから成立確認）

### P1（収益化の最低条件）

- quote更新（価格差閾値で cancel&replace）
- 部分約定/未ヘッジの追いかけ・unwind（max_unhedged_sec）
- reject streak で HALT

### P2（改善）

- Inventory skew（持ちすぎたらクォート非対称）
- Fundingバイアスの最適化（方向・閾値・クールダウン）
- Basisフィルタ（perp_mid - spot_mid）を追加

## 12. 付録：WS subscribeテンプレ（そのまま貼れる）

### 12.1 Public books5（SPOT + PERP）

形式は op=subscribe, args=[{instType, channel, instId}]

```json
{
  "op": "subscribe",
  "args": [
    { "instType": "SPOT", "channel": "books5", "instId": "ETHUSDT" },
    { "instType": "USDT-FUTURES", "channel": "books5", "instId": "ETHUSDT" }
  ]
}
```

### 12.2 Private fill（SPOT / USDT-FUTURES）

instId は default で全銘柄購読できる例が公式にある

```json
{
  "op": "subscribe",
  "args": [
    { "instType": "SPOT", "channel": "fill", "instId": "default" },
    { "instType": "USDT-FUTURES", "channel": "fill", "instId": "default" }
  ]
}
```

## 13. “この設計の答え”を一言で言うと

- PERPで post_only MM（OBIで歪める）
- PERPが刺さったら SPOTでIOCヘッジ
- Fundingで稼働方向と稼働可否を決める
- そして 未ヘッジ露出を絶対に放置しない（fail-closed）


## 14. 追記（修正/補足）

- PERPのTIFパラメータは `timeInForceValue` に統一する。`force` はSPOT専用として扱い、PERPはgatewayで必ず `timeInForceValue` を送る。
- Public bookは `books5` が前提だが、片側が `books` しか通らない可能性がある。起動時に購読成否をログし、`books5` 失敗時は `books` へフォールバックする。
- WS再接続は fail-closed: 切断/再接続の間はquoteを全キャンセルし、books/fundingがfreshになるまでHALT。
- 受け入れ条件の simulated 判定は、`simulated:false` だけでなく `simulated` フィールド欠落も「実WS扱い」とする。
- 既存の `bot/marketdata/book.py`, `bot/marketdata/funding.py`, `bot/strategy/mm_funding.py` は再利用/置換の方針を明記し、重複実装を避ける（新規 `bot/marketdata/bitget_md.py` を採用するなら旧コードは deprecated）。

## 15. 実装チェックリスト＋受け入れ条件＋ファイル単位タスク（Codex貼り付け用）

### 15.1 Codexに貼る指示文（修正版）

Bitget ETHUSDT Spot + USDT-Futures Perp のデルタ中立MM Botを pybotters で実装/整理する。

前提:
- 既存で「perp fill → spot hedge 発火」は成立している（ただし spot fill が simulated:true の疑似モードが残っている）
- 目標は simulated を剥がし、Bitget private WS の実 fill で hedge 完了まで成立させる

要求:
1) Poetryプロジェクトとして動く構成にする（entrypoint scriptsあり）
2) モジュール分割（責務を明確化）
   - bot/config.py: env設定
   - bot/log/jsonl.py: JSONL logger
   - bot/exchange/bitget_gateway.py: REST + WS + constraints + funding + posMode確認
   - bot/marketdata/bitget_md.py: books5購読→BBO/OBI算出（books5不可ならbooksへフォールバック）
   - bot/oms/oms.py: quote管理、fill正規化、spot orderId ↔ clientOid マップ、hedge残量追跡
   - bot/strategy/mm_obi_funding.py: quote価格計算（MM + OBI + funding bias）
   - bot/app.py: wiring / task起動 / shutdown
   - 既存の bot/marketdata/book.py, bot/marketdata/funding.py, bot/strategy/mm_funding.py は再利用/置換の方針を明記し、重複を避ける

3) P0の必須:
   - SIMULATED_SPOT_FILL をデフォルトOFFにする（= simulated を剥がす）
   - spot fill は clientOid が来ない想定で、place-order応答の orderId を保存し fill.orderId と突合して clientOid を復元する
   - fill冪等（instType + tradeId を第一キー、無い場合のfallbackも用意）
   - posMode を起動時に検査し、想定と違う場合は fail-closed（必要ならAUTO_SETで変更試行も可）
   - PERP の time-in-force パラメータは `timeInForceValue` に統一（SPOTは `force`）
   - WS再接続時は fail-closed（切断中はquote全キャンセル、再開時はfreshデータ待ち）

4) P1:
   - 部分約定に耐えるヘッジ残量追跡（want_qty / filled_qty / remain / deadline）
   - max_unhedged_sec / max_unhedged_notional 超過時の行動（cancel quotes → chase IOC → 最後は unwind）
   - quote cancel/replace を抑制（閾値）

5) ログ:
   - すべての order_new / order_cancel / order_skip / fill / tick / risk / state を JSONL に吐く
   - intent/source/mode/reason/leg/cycle_id を必ず入れる

6) 受け入れ条件:
   - DRY_RUN=1 で quotes が出る（order_new intent=quote）
   - DRY_RUN=0 で perp fill が来たら spot hedge の order_new(intent=hedge) が必ず出る
   - spot fill が simulated:true ではなく WS 由来で入り、hedge残量が 0 になる
     - simulated フィールドが無い/false のどちらもOK
   - 同一fillで hedge が二重発火しない（dedupeできている）

### 15.2 実装タスク（P0 / P1 / P2）＋受け入れ条件

P0 最優先：実WSで「perp fill → spot hedge 完了」まで

P0-1 simulated を剥がす

- SIMULATED_SPOT_FILL（env）を作る
- default=0（本番はWS fillのみ）
- テスト/疑似用に 1 でだけ生成してよい

受け入れ条件

- simulated:true のログが出ない状態で hedge 完了できる

P0-2 spot fill の clientOid を “orderId マップ” で復元

- Spotの private fill には clientOid が来ない前提で設計（現状もここ）
- spot place-order のレスポンスから orderId を取り、
  - spot_clientOid -> spot_orderId
  - spot_orderId -> spot_clientOid
  をOMSに保存
- Spot fill を受け取ったら orderId で突合して clientOid を復元し、ヘッジ残量を減らす

受け入れ条件

- fill(SPOT) のログに clientOid が最終的に埋まる（復元結果）

P0-3 fill の冪等（dedupe）

一意キー：

- (instType, tradeId) が取れるならこれ
- 取れないなら (instType, orderId, ts, price, qty) の合成

受け入れ条件

- WS再接続・バーストでも hedge が二重に出ない

P0-4 posMode 検査（40774再発防止）

- 起動時に posMode を取得してログ
- TARGET_POS_MODE が設定されていて違うなら
  - AUTO_SET_POS_MODE=1 のときだけ変更を試す
  - 失敗したら 即停止（fail-closed）

受け入れ条件

- 40774 が出たときに “黙って進む” ことが無く、停止/明示ログが出る

P0-5 TIFパラメータ統一 + books5フォールバック + WS再接続

- PERPの time-in-force は `timeInForceValue` に統一し、`force` はSPOTのみで使用
- public bookは `books5` を優先し、購読失敗/無更新時は `books` へフォールバック
- WS再接続時は quote を全キャンセルし、book/funding が fresh になるまでHALT

受け入れ条件

- PERP注文のTIFが統一されている（ログで確認）
- books5 失敗時に自動で `books` へ切り替わり、book_stale にならない
- WS切断中にquoteが残留しない

P1：事故らない（未ヘッジ露出・部分約定）

P1-1 ヘッジ残量追跡（部分約定対応）

HedgeTicket を導入：

- hedge_id（=clientOid）
- want_qty
- filled_qty
- remain
- deadline_ms
- tries
- status

受け入れ条件

- Spot fill が部分約定でも remain が 0 になるまで追跡できる
- 期限超過で追いかけ（chase IOC）する

P1-2 max_unhedged の「行動」を実装

超過時の段階：

- quotes cancel（新規リスク停止）
- hedge chase（より aggressive）
- それでもダメなら unwind（perp側を reduceOnly market 等で露出消し）

受け入れ条件

- unhedged_notional が閾値超えたまま放置されない

P1-3 quote update の抑制（cancel/replaceが多すぎると負ける）

- 「価格が X bps 以上動いたら置換」など閾値導入

受け入れ条件

- order_cancel/order_new の連打が落ちる

P2：勝てる/調整しやすい（計測）

- hedge_latency_ms（perp fill→spot fill）
- max_unhedged_notional の最大値
- quote_replace_count
- reject_streak の統計

受け入れ条件：ログだけで改善点が見える

### 15.3 推奨ファイル構成（最小で“保守できる”単位）

bot/
  app.py
  config.py
  types.py                 # 共通型（BBO, Fill, OrderIntent, etc）
  log/
    jsonl.py
  exchange/
    bitget_gateway.py
  marketdata/
    bitget_md.py
  oms/
    oms.py
  strategy/
    mm_obi_funding.py
  risk/
    guards.py

### 15.4 各ファイルの“責務と最小I/F”（Codexが迷わないための仕様）

bot/types.py

- BBO, BookMetrics(obi), NormalizedFill, OrderRequest, OrderResult
- OrderIntent = quote|hedge|unwind|flatten
- Mode = IDLE|QUOTING|HEDGING|COOLDOWN|HALTED

bot/exchange/bitget_gateway.py

- 責務：Bitgetへの入出力を全部ここに隔離
- connect_public_ws(store) / connect_private_ws(store)
- subscribe_books5(symbol) / subscribe_private(...)
- get_funding_rate(symbol)
- load_constraints(symbol)
- get_pos_mode(productType) / set_pos_mode(productType, posMode)
- place_perp_order(data) / cancel_perp_order(data)
- place_spot_order(data) / cancel_spot_order(data)
- PERPは timeInForceValue を使用し、上位層には抽象化されたI/Fを提供

bot/marketdata/bitget_md.py

- 入力：store.book
- 出力：MarketSnapshot(spot_bbo, perp_bbo, obi, ts)
- books5前提（top5）で OBIを算出
- books5不可なら books へフォールバック

bot/strategy/mm_obi_funding.py

- 入力：MarketSnapshot + funding + positions + guards
- 出力：QuotePlan（bid_px/ask_px/qty/理由）

bot/oms/oms.py

- 責務：注文の整合性・冪等・ヘッジ追跡
- quoteは常に「bid 1本 / ask 1本」
- perp fill を受けたら hedge ticket 作成 → spot IOC を出す
- spot fill を受けたら ticket を減らす（orderId→clientOid復元）
- dedupeで二重発火を潰す
- 未ヘッジ超過で unwind() 呼び出し

### 15.5 受け入れテスト（手元で“OK/NG”が一発で分かる）

#### 15.5.1 dry-run

DRY_RUN=1 で起動

条件：

- order_new(intent=quote, dry_run=true) が出る
- tick が連続で出る
- order_skip constraints_missing が出ない

#### 15.5.2 live（超小ロット）

DRY_RUN=0

条件：

- fill(instType=USDT-FUTURES) が来たら必ず
  order_new(intent=hedge, leg=spot_ioc) が出る
- その後 fill(instType=SPOT) が simulated なし/false で来る
- hedge ticket の remain が 0 になるログが出る
- 同一fillで hedge が二重発火しない
