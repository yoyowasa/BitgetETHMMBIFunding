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

### ゴールとして追加（収益構造の最低限計器）

- PnL5項目の分解ログ（gross_spread / fees / funding / hedge_slip / basis）をJSONLに記録する
  → パラメータ調整を事実ベースで行うための必須計器。完全な会計ではなく運用上の損益モニタリング。

## 1. システム全体像（コンポーネントと責務）

### 1.1 コンポーネント

#### BitgetMarketData（WS Public）

- books5 を購読して SPOT/PERP の板（上位5段）を受信
- BBO（best bid/ask）と OBI（Order Book Imbalance）を算出
- trade チャンネルを購読して成行フロー（TFI）を集計し adverse selection 検知に使う
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

#### micro_price（予約価格の土台）

板厚加重 mid を使う。naive な mid より adverse selection を減らせる：

```
bid_liq = Σ bid_size（上位N段）
ask_liq = Σ ask_size（上位N段）
micro_price = (bid_px_best * ask_liq + ask_px_best * bid_liq) / (bid_liq + ask_liq)
```

#### TFI（Trade Flow Imbalance）

直近 T 秒の成行フロー不均衡（trade チャンネルから集計）：

```
tfi = (buy_vol - sell_vol) / (buy_vol + sell_vol + eps)  # [-1, +1]
```

#### 予約価格（reservation price）

MVP は OBI のみ。P1 以降で TFI を追加：

```
# MVP
r = micro_price * (1 + k_obi * obi)

# P1以降（A-S在庫ペナルティ + TFI）
r = micro_price
    - q * gamma * sigma^2 * T_horizon   # 在庫ペナルティ（q: 在庫量、gamma: リスク回避度）
    + k_obi * obi * micro_price
    + k_tfi * tfi * micro_price          # 動的フロー
    + k_funding * funding_bias * micro_price
```

例：obi が + なら r を上げ、買い側を積極化/売り側を消極化

#### half spread と下限ガード（必須）

```
h_raw = base_half_spread_bps + inventory_skew_bps + funding_skew_bps

# 構造的赤字を防ぐ絶対下限
# (fee_perp_maker=2bps + fee_spot_taker=10bps + hedge_slip_avg=2bps) / 2 + safety_margin=1bps
MIN_HALF_SPREAD_BPS = 8.0  # VIP0前提。手数料ティア改善時は下げてよい

h = max(h_raw, MIN_HALF_SPREAD_BPS)
```

**この下限を割ったクォートは構造的に赤字。OBI/Funding スキューが逆方向に振れても絶対に割らない。**

#### 価格

```
bid_px = r * (1 - h / 10000)
ask_px = r * (1 + h / 10000)
```

#### 注文

- PERP bid: side=buy, orderType=limit, timeInForceValue=post_only
- PERP ask: side=sell, orderType=limit, timeInForceValue=post_only

books5 は snapshot が来るので更新は “価格差が reprice_threshold_bps 超えたらcancel&replace”

#### reprice 抑制

```
# 微小な動きで cancel/replace を連打しない
if abs(new_px - current_order_px) / current_order_px * 10000 < reprice_threshold_bps:
    skip  # キャンセルしない
```

### 5.4 Funding の使い方

#### 前提：ETHUSDTのFundingは小さい

ETHUSDTの典型的なFunding rate は 0.004〜0.006%/8h ≒ 1〜1.5bps/day 程度。
往復の取引コスト（12bps以上）を Funding だけで回収するのは不可能。

**Funding は「スプレッド収益への追い風ボーナス」であり、メイン収益源ではない。**

#### フィルタ

```
# Fundingがノイズレベル以下は稼働しない
if abs(funding_rate) < min_abs_funding:   # 例: 0.005%/8h
    halt_quoting()
```

#### クォートの非対称化（MVP）

Fundingが「ショート有利（funding_rate > 0、ロングがショートに支払う）」なら：
- ask をやや aggressive（PERP ショートを積みやすく）
- bid をやや passive

```python
funding_skew_bps = k_funding * funding_rate_bps  # funding_rate を bps 換算して乗算
# funding_rate > 0: ask 側を下げ、bid 側を上げ（ショート方向）
# funding_rate < 0: 逆方向
```

#### 在庫目標の Funding バイアス（P2）

Funding の受け取り側に PERP 在庫を「ゆっくり」積む：

```python
funding_apr_bps = funding_rate * 3 * 365 * 10000  # 年率換算

if funding_rate > 0:   # ショート保持で受取
    target_perp_inventory = -q_max * min(1.0, funding_apr_bps / target_apr_bps)
elif funding_rate < 0:
    target_perp_inventory = +q_max * min(1.0, abs(funding_apr_bps) / target_apr_bps)

# inventory_skew は 0 ではなく target_perp_inventory を中立点として計算
inventory_skew_bps = k_inv * (q - target_perp_inventory)
```

#### Funding ウィンドウ戦略（P2）

各 8h Funding settle（00:00 / 08:00 / 16:00 UTC）の直前 N 分だけ在庫を傾けて、
settle 後に中立へ戻す。デルタ中立キャリーの利回りを最大化できる。

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

```
px = spot_ask * (1 + hedge_slip_bps / 10000)
```

sell hedge:

```
px = spot_bid * (1 - hedge_slip_bps / 10000)
```

price_precision で丸め、min_trade_usdt を満たさないなら order_skip（fail-closed）

### 6.5（追記）ヘッジコスト動的化とラダー戦略（P2）

現状は hedge_slip_bps 固定で IOC 一択。P2 で以下に昇格：

```python
# 板厚からインパクトコストを事前見積もり
impact_bps = estimate_walk_the_book(spot_book, hedge_size)

# 未ヘッジ時間に余裕があれば post_only で待つ（手数料リベートも狙える）
if unhedged_sec < max_unhedged_sec * 0.5 and impact_bps > impact_threshold_bps:
    place_spot_postonly_at_best()
else:
    # スリッページ上限は板厚見積もりとhyper_slip_bpsの大きい方
    slip = max(hedge_slip_bps, impact_bps + hedge_slip_buffer_bps)
    place_spot_ioc(px_with_slip=slip)
```

SPOT テイカー 0.1% → メイカー 0.08% の差 + スリッページ削減で月次ヘッジコストが 30〜50% 下がる。

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
- max_inventory_notional（追加）  
  abs(perp_pos_notional) > max_inventory_notional（unhedged_exposure とは独立したハード上限）  
  → 新規クォートを止め、在庫を縮小する方向のクォートのみ許可  
  目安: 口座の 5〜10%（例: 口座 $1000 なら $50〜$100）
- spread_below_min（追加）  
  h_calculated < MIN_HALF_SPREAD_BPS （下限ガードが発動した場合はログに記録）  
  → h を MIN_HALF_SPREAD_BPS に強制。構造的赤字クォートを絶対に出さない

#### adverse selection ガード（P1追加）

trade チャンネルの成行フローを監視し、以下の条件で即時クォート引き：

| ガード | 条件 | アクション |
|---|---|---|
| quote_fade_on_fast_mid | 直近100ms の mid 変化 > fade_vol_bps | 両クォートを即 cancel |
| cancel_on_aggressive_trade | 自クォート近傍で反対側大口成行が入った | 該当クォートを即 cancel |
| asymmetric_fade | TFI が片側に強く偏った（> tfi_fade_threshold） | 偏り方向のクォートを 1 tick 退避 |

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

### 10.3 PnL 分解ロガー（計器類・必須）

以下の5項目を 1 分足で集計して JSONL に出力する。完全な税務会計ではなく、
パラメータ調整を事実ベースで行うための運用計器。

```python
pnl_components = {
    "ts_min": int,               # 分足タイムスタンプ
    "gross_spread_pnl": float,   # Σ (perp_sell_px - perp_buy_px) * qty（両側約定分）
    "fees_paid": float,          # Σ perp_maker_fee + spot_taker_fee（USDT）
    "funding_received": float,   # Σ funding_rate * perp_pos * notional（8h決済ごと）
    "hedge_slip_cost": float,    # Σ (spot_fill_px - spot_mid_at_fill) * qty（buy hedgeは正値が損失）
    "basis_pnl": float,          # (perp_mid - spot_mid) の変化 × 残ポジ（MTM）
}
# event="pnl_1min" で JSONL に追記
```

これがあれば `base_half_spread_bps`, `k_obi`, `gamma` の調整が数値で判断できる。
**gross_spread_pnl < fees_paid の状態が続くなら MIN_HALF_SPREAD_BPS を上げる。**

## 11. 実装順（Bitgetで死ににくい順）

### P0（今すぐ）

- 起動時：constraintsロード（spot/perp）→ constraints_missingなら停止
- 起動時：GET /mix/account/account で posMode を確認（想定と違うなら停止 or 変更試行）
- WS接続：public books5（spot+perp）+ public trade（perp）+ private fill/orders/positions
- QUOTE: PERPにpost_only両建てを出す（最小サイズ）、MIN_HALF_SPREAD_BPS の下限ガードを有効化
- PERP fill を受けたら SPOT IOC hedge（1回でいいから成立確認）

### P1（収益化の最低条件）

- quote更新（reprice_threshold_bps で cancel&replace 抑制）
- 部分約定/未ヘッジの追いかけ・unwind（max_unhedged_sec）
- reject streak で HALT
- max_inventory_notional ハード上限ガード
- adverse selection 防御：quote_fade_on_fast_mid / cancel_on_aggressive_trade / asymmetric_fade
- PnL 分解ロガー（gross_spread/fees/funding/hedge_slip/basis の 1 分足集計）
- micro_price + TFI を予約価格に統合

### P2（改善）

- Inventory skew（持ちすぎたらクォート非対称）、Funding target_perp_inventory
- Funding ウィンドウ戦略（settle 直前集中保有）
- Basis ロガー → quote への反映
- ヘッジラダー化（post_only 段階ヘッジ + IOC フォールバック）
- A-S 型在庫ペナルティ項（gamma, sigma EWMA）
- 手数料ティア最適化（BGB割引・MMプログラム申請）

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

### 12.2 Public trade（PERP）— adverse selection 用

成行フロー（TFI算出と cancel-on-aggressive-trade）に使う。books5 と同時に購読する。

```json
{
  "op": "subscribe",
  "args": [
    { "instType": "USDT-FUTURES", "channel": "trade", "instId": "ETHUSDT" }
  ]
}
```

受信フィールド: price, size, side（buy/sell）, ts

TFI 集計（直近 T 秒ウィンドウ、ローリング）：

```python
# side="buy" → buy_vol 累積、side="sell" → sell_vol 累積
tfi = (buy_vol - sell_vol) / (buy_vol + sell_vol + eps)
```

### 12.3 Private fill（SPOT / USDT-FUTURES）

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

## 13. 推奨パラメータ初期値（ETHUSDT・VIP0前提）

| パラメータ | 推奨初期値 | 根拠 |
|---|---|---|
| `base_half_spread_bps` | 8.0 | (2+10+2)/2 + 1 safety = 8bps |
| `MIN_HALF_SPREAD_BPS` | 8.0 | 構造的赤字の絶対防止ライン（=base と同値でよい） |
| `reprice_threshold_bps` | 1.0 | これ未満の価格変化ではcancel/replaceしない |
| `k_obi` | 0.5 | 強すぎると毒性フロー追随（0.3〜0.8で調整） |
| `k_tfi` (P1) | 0.5 | 1秒ウィンドウで正規化 |
| `k_funding` | 0.3 | Fundingバイアスの感度 |
| `gamma` (P1, A-S) | 0.2 | 小さいほどアグレッシブ |
| `tfi_window_sec` | 5 | TFI集計ウィンドウ |
| `fade_vol_bps` | 3.0 | 100ms mid変化でquote引きするしきい値 |
| `min_abs_funding` | 0.005% / 8h | これ未満はノイズ（稼働停止） |
| `hedge_slip_bps` | 2.0 | IOCヘッジのスリッページ許容（初期値） |
| `hedge_slip_buffer_bps` (P2) | 1.0 | 板厚見積もりへの上乗せバッファ |
| `impact_threshold_bps` (P2) | 4.0 | これ超えたらpost_onlyへフォールバック |
| `max_unhedged_sec` | 5 | これを超えると IOC 強制 |
| `max_unhedged_notional` | 口座の 0.5% | 例: $1000口座 → $5 |
| `max_inventory_notional` | 口座の 5〜10% | 例: $1000口座 → $50〜$100 |
| `quote_size` | min_trade_usdt × 1.2 | 板拒否を避ける最小サイズ |
| `tfi_fade_threshold` | 0.6 | TFI がこれを超えたら asymmetric fade |

**手数料前提（VIP0）：**
- PERP maker: 0.02%（2bps）
- SPOT taker: 0.10%（10bps）
- BGB保有で20%割引 → PERP maker 1.6bps, SPOT taker 8bps
  → MIN_HALF_SPREAD_BPS を 7.0 に下げられる
- MMプログラム（申請制）でマイナスメイカーリベート取得可能

**MMプログラム / 手数料ティア最適化（運用タスク）：**
稼働前に以下を実施することで月次PnLが 20〜40% 改善しうる：
1. BGB（Bitget Token）保有 → 手数料 20% 割引
2. Bitget MM プログラムにメール申請 → マイナスリベートスロット候補
3. 月次取引量が 500万USDT を超えると VIP1（maker 0.015%）

## 14. “この設計の答え”を一言で言うと

- PERPで post_only MM（OBIで歪める）
- PERPが刺さったら SPOTでIOCヘッジ
- Fundingで稼働方向と稼働可否を決める
- 未ヘッジ露出を絶対に放置しない（fail-closed）
- MIN_HALF_SPREAD_BPS を割るクォートは出さない（経済的 fail-closed）

## 15. 追記（修正/補足）

- PERPのTIFパラメータは `timeInForceValue` に統一する。`force` はSPOT専用として扱い、PERPはgatewayで必ず `timeInForceValue` を送る。
- Public bookは `books5` が前提だが、片側が `books` しか通らない可能性がある。起動時に購読成否をログし、`books5` 失敗時は `books` へフォールバックする。
- WS再接続は fail-closed: 切断/再接続の間はquoteを全キャンセルし、books/fundingがfreshになるまでHALT。
- 受け入れ条件の simulated 判定は、`simulated:false` だけでなく `simulated` フィールド欠落も「実WS扱い」とする。
- 既存の `bot/marketdata/book.py`, `bot/marketdata/funding.py`, `bot/strategy/mm_funding.py` は再利用/置換の方針を明記し、重複実装を避ける（新規 `bot/marketdata/bitget_md.py` を採用するなら旧コードは deprecated）。

## 16. 実装チェックリスト＋受け入れ条件＋ファイル単位タスク（Codex貼り付け用）

### 16.1 Codexに貼る指示文（修正版）

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
   - public trade チャンネル（PERP）を books5 と同時に購読し TFI を集計する
   - half spread の下限ガード（MIN_HALF_SPREAD_BPS）を quote 計算に組み込む

4) P1:
   - 部分約定に耐えるヘッジ残量追跡（want_qty / filled_qty / remain / deadline）
   - max_unhedged_sec / max_unhedged_notional / max_inventory_notional 超過時の行動（cancel quotes → chase IOC → 最後は unwind）
   - quote cancel/replace を抑制（reprice_threshold_bps）
   - adverse selection ガード（quote_fade_on_fast_mid / cancel_on_aggressive_trade / asymmetric_fade）
   - PnL 分解ロガー（event=pnl_1min：gross_spread/fees/funding/hedge_slip/basis）
   - micro_price + TFI を予約価格に統合

5) ログ:
   - すべての order_new / order_cancel / order_skip / fill / tick / risk / state を JSONL に吐く
   - intent/source/mode/reason/leg/cycle_id を必ず入れる

6) 受け入れ条件:
   - DRY_RUN=1 で quotes が出る（order_new intent=quote）
   - DRY_RUN=0 で perp fill が来たら spot hedge の order_new(intent=hedge) が必ず出る
   - spot fill が simulated:true ではなく WS 由来で入り、hedge残量が 0 になる
     - simulated フィールドが無い/false のどちらもOK
   - 同一fillで hedge が二重発火しない（dedupeできている）

### 16.2 実装タスク（P0 / P1 / P2）＋受け入れ条件

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

### 16.3 推奨ファイル構成（最小で”保守できる”単位）

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

### 16.4 各ファイルの”責務と最小I/F”（Codexが迷わないための仕様）

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

### 16.5 受け入れテスト（手元で”OK/NG”が一発で分かる）

#### 16.5.1 dry-run

DRY_RUN=1 で起動

条件：

- order_new(intent=quote, dry_run=true) が出る
- tick が連続で出る
- order_skip constraints_missing が出ない

#### 16.5.2 live（超小ロット）

DRY_RUN=0

条件：

- fill(instType=USDT-FUTURES) が来たら必ず
  order_new(intent=hedge, leg=spot_ioc) が出る
- その後 fill(instType=SPOT) が simulated なし/false で来る
- hedge ticket の remain が 0 になるログが出る
- 同一fillで hedge が二重発火しない

## 17. 多角的分析による追加修正仕様（2026-04-24 コードレビュー反映）

### 17.1 発見された最重要問題：現行パラメータは構造的赤字

```
収入:   2 × base_half_spread_bps = 2 × 2.0 = 4.0 bps
コスト: fee_maker_perp + fee_taker_spot + slippage = 1.0 + 6.0 + 3.0 = 10.0 bps
期待PnL/往復 = 4.0 - 10.0 = -6.0 bps（構造的赤字）
```

`fee_maker_perp_bps=1.0`, `fee_taker_spot_bps=6.0` は VIP1-2 優遇前提。
VIP0 実態（2bps/10bps）で再計算すると **-10bps/往復**。

**DRY_RUN=0 投入前に必ず修正が必要。**

### 17.2 修正仕様（実装対象）

#### A-1: MIN_HALF_SPREAD_BPS 下限ガード（最優先）

【ファイル】`bot/strategy/mm_funding.py`（L282-284 付近）

【変更前】
```python
h = base_half_spread_bps * (2 if unhedged else 1)
```

【変更後】
```python
MIN_HALF_SPREAD_BPS = cfg.get("strategy.min_half_spread_bps", 8.0)
h_raw = base_half_spread_bps * (2 if unhedged else 1)
h = max(h_raw, MIN_HALF_SPREAD_BPS)
if h_raw < MIN_HALF_SPREAD_BPS:
    logger.log(event="risk", reason="spread_below_min", h_raw=h_raw, h=h)
```

【理由】(2+10+2)/2 + 1 = 8bps が VIP0 前提の損益分岐点。これを割るクォートは構造的赤字。
【確認手順】DRY_RUN=1 で order_new の bid_px / ask_px 差が 16bps 以上であること。

---

#### A-2: config.yaml の手数料前提を VIP0 実態値に修正

【ファイル】`config.yaml`（cost + strategy セクション）

【変更前】
```yaml
cost:
  fee_maker_perp_bps: 1.0
  fee_taker_spot_bps: 6.0
  slippage_bps: 3.0
strategy:
  base_half_spread_bps: 2.0
```

【変更後】
```yaml
cost:
  # VIP0 前提（BGB 20%割引未適用）。実 tier は /api/v2/user/fee で確認すること
  fee_maker_perp_bps: 2.0   # VIP0: 0.02%
  fee_taker_spot_bps: 10.0  # VIP0: 0.10%
  slippage_bps: 2.0          # 実測後に調整
strategy:
  base_half_spread_bps: 8.0  # 損益分岐点以上に引き上げ
  min_half_spread_bps: 8.0   # 下限ガード値（config 化）
```

【理由】実 tier と異なる cost 前提では EV 計算が全て狂う。

---

#### A-3: symbol 単位 asyncio.Lock（OMS 排他）

【ファイル】`bot/oms/oms.py`

【変更前】排他制御なし（async Task のみ）

【変更後】
```python
class OMS:
    def __init__(self, ...):
        self._symbol_locks: dict[str, asyncio.Lock] = {}

    def _get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._symbol_locks:
            self._symbol_locks[symbol] = asyncio.Lock()
        return self._symbol_locks[symbol]

    async def place_quote(self, symbol, ...):
        async with self._get_symbol_lock(symbol):
            ...

    async def flatten(self, symbol, ...):
        async with self._get_symbol_lock(symbol):
            ...
```

【理由】§7.3 の設計を実装に反映。flatten と通常 close の並走による二重決済事故を構造的に防止。

---

#### B-1: public trade チャンネル購読

【ファイル】`bot/exchange/bitget_gateway.py`（subscribe 処理）

【変更前】books5 のみ購読

【変更後】books5 と同時に追加購読：
```json
{ "instType": "USDT-FUTURES", "channel": "trade", "instId": "ETHUSDT" }
```

【未確定点】trade チャンネルのメッセージフィールド名を Bitget WS ドキュメントで要確認。

---

#### B-2: adverse selection ガード 3 点セット

【ファイル】`bot/risk/guards.py` + `bot/strategy/mm_funding.py`

```python
def check_fast_mid_move(mid_now, mid_100ms_ago, fade_vol_bps=3.0) -> bool:
    """True なら両クォートを即 cancel すべき"""
    return abs(mid_now - mid_100ms_ago) / mid_100ms_ago * 10000 > fade_vol_bps

def check_aggressive_trade(trade_px, trade_side, bid_px, ask_px, proximity_bps=1.0) -> str | None:
    """自クォート近傍で反対成行が入った場合、cancel すべき leg を返す"""
    if trade_side == "buy" and abs(trade_px - ask_px) / ask_px * 10000 < proximity_bps:
        return "ask"
    if trade_side == "sell" and abs(trade_px - bid_px) / bid_px * 10000 < proximity_bps:
        return "bid"
    return None

def check_tfi_fade(tfi: float, threshold=0.6) -> str | None:
    """TFI が偏ったら偏り方向のクォートを 1tick 退避"""
    if tfi > threshold:
        return "ask"
    if tfi < -threshold:
        return "bid"
    return None
```

【確認手順】DRY_RUN=1 でガード発火時に event=risk reason=quote_fade / cancel_aggressive / tfi_fade が出ること。

---

#### B-3: TFI 集計モジュール

【ファイル】新規 `bot/marketdata/tfi.py`

```python
from collections import deque

class TFIAccumulator:
    def __init__(self, window_sec: float = 5.0):
        self._window = window_sec
        self._trades: deque[tuple[float, float, str]] = deque()  # (ts, size, side)

    def add_trade(self, ts: float, size: float, side: str) -> None:
        self._trades.append((ts, size, side))
        cutoff = ts - self._window
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()

    def get_tfi(self) -> float:
        buy_vol = sum(s for _, s, d in self._trades if d == "buy")
        sell_vol = sum(s for _, s, d in self._trades if d == "sell")
        total = buy_vol + sell_vol
        return (buy_vol - sell_vol) / total if total > 0 else 0.0
```

---

#### C-1: PnL 分解ロガー

【ファイル】新規 `bot/log/pnl_logger.py` + `bot/app.py`（1分タスク追加）

```python
class PnLAggregator:
    def __init__(self, logger):
        self._logger = logger
        self._reset()

    def _reset(self):
        self.gross_spread_pnl = 0.0   # Σ (perp_sell - perp_buy) * qty
        self.fees_paid = 0.0           # Σ perp_maker_fee + spot_taker_fee
        self.funding_received = 0.0    # Σ funding_rate * pos * notional
        self.hedge_slip_cost = 0.0     # Σ |spot_fill_px - spot_mid_at_fill| * qty
        self.basis_pnl = 0.0           # MTM: (perp_mid - spot_mid) 変化 × 残ポジ

    def flush(self):
        self._logger.log(
            event="pnl_1min",
            gross_spread_pnl=self.gross_spread_pnl,
            fees_paid=self.fees_paid,
            funding_received=self.funding_received,
            hedge_slip_cost=self.hedge_slip_cost,
            basis_pnl=self.basis_pnl,
            net_pnl=(self.gross_spread_pnl - self.fees_paid
                     + self.funding_received - self.hedge_slip_cost + self.basis_pnl),
        )
        self._reset()
```

**判断基準**：`gross_spread_pnl < fees_paid` が続くなら `MIN_HALF_SPREAD_BPS` を上げる。

---

#### C-2: reprice 抑制

【ファイル】`bot/oms/oms.py`（update_quotes 処理）

```python
REPRICE_THRESHOLD_BPS = cfg.get("strategy.reprice_threshold_bps", 1.0)

def _should_reprice(self, current_px: float, new_px: float) -> bool:
    return abs(new_px - current_px) / current_px * 10000 >= REPRICE_THRESHOLD_BPS
```

quote 更新前に `_should_reprice` チェックを追加し、False なら cancel/replace をスキップ。

### 17.3 未確定点

| 項目 | 確認内容 |
|---|---|
| 実際の VIP tier | `/api/v2/user/fee` で現行手数料率を確認 |
| basis_pnl 計算式 | 「保有ポジ × 乖離変化」か「累積乖離」か |
| trade チャンネルのフィールド名 | Bitget WS ドキュメントで `channel=trade` のフィールドを確認 |
| max_unhedged_sec 緩和 | 2.0秒 → 5.0秒への緩和の可否（リスク許容度依存） |

## 18. 実装チェックリスト（Phase A / B / C / D / E）

### Phase A — ライブ投入前の絶対条件（DRY_RUN=0 の前に完了必須）

- [ ] **A-1** `MIN_HALF_SPREAD_BPS` 下限ガードを `mm_funding.py` に実装
  - 受入: DRY_RUN=1 で bid_px/ask_px 差が 16bps 以上
  - 受入: `spread_below_min` ログが出る
- [ ] **A-2** `config.yaml` の手数料を VIP0 実態値に修正（2.0/10.0）、`base_half_spread_bps=8.0`、`min_half_spread_bps=8.0` 追加
  - 受入: 設定読み込みテストが通る
- [ ] **A-3** `oms.py` に `symbol_locks` (asyncio.Lock) 追加、全発注パスで取得
  - 受入: flatten と place_quote が同一 symbol で並走しない
- [ ] **A-確認** Bitget VIP tier を `/api/v2/user/fee` で取得してログ出力、cost config と照合

### Phase B — adverse selection 封じ（A 完了後）

- [ ] **B-1** `bitget_gateway.py` に PERP trade チャンネル購読を追加（books5 と同時）
  - 受入: event=tick に tfi フィールドが入る
- [ ] **B-2** `guards.py` に 3 点 adverse selection ガードを実装
  - `check_fast_mid_move`（100ms mid変化 > fade_vol_bps で両cancel）
  - `check_aggressive_trade`（自クォート近傍の反対大口成行で即cancel）
  - `check_tfi_fade`（TFI > 0.6 で偏り方向のクォートを1tick退避）
  - 受入: DRY_RUN=1 でガード発火時に event=risk reason=quote_fade / cancel_aggressive / tfi_fade が出る
- [ ] **B-3** `bot/marketdata/tfi.py` 新規作成（5秒ウィンドウ TFI 集計）
  - 受入: tfi 値が [-1, +1] 内、ウィンドウ外トレードが除外される

### Phase C — 計測・観測（B 完了後）

- [ ] **C-1** `bot/log/pnl_logger.py` 新規作成、`app.py` に 1 分足集計タスク追加
  - 受入: event=pnl_1min が 60 秒毎に出力
  - 受入: gross_spread_pnl / fees_paid / net_pnl の符号が論理的に正しい
- [ ] **C-2** `oms.py` に `reprice_threshold_bps`（デフォルト 1.0bps）抑制を実装
  - 受入: 1bps 未満の price 変化で order_cancel が出ない
- [ ] **C-3** fill latency（perp_fill → spot_fill の ms）を fill ログに追記
- [ ] **C-4** quote fill 率・adverse fill 率（fill 後 N秒 mid move）を記録

### Phase D — 収益最適化（C 完了後、PnL 数値を確認してから着手）

- [ ] **D-1** 予約価格を naive mid → micro_price（板厚加重mid）に置換
- [ ] **D-2** `funding_skew_bps` 実装（bps換算 funding を half spread に加算）
- [ ] **D-3** `target_perp_inventory`（Funding 受取方向の在庫目標）実装
- [ ] **D-4** ヘッジラダー化（unhedged_sec < max * 0.5 で post_only、超過で IOC）
- [ ] **D-5** Funding ウィンドウ戦略（8h settle 直前 N 分だけ在庫傾け）
- [ ] **D-6** micro_price + TFI を予約価格に統合（A-S モデル簡易版）

### Phase E — 運用タスク（コード不要）

- [ ] **E-1** BGB 少量保有で手数料 20% 割引を有効化（有効後: min_half_spread_bps=7.0 に引き下げ可）
- [ ] **E-2** Bitget MM プログラムにメール申請（マイナスリベートスロット候補）
- [ ] **E-3** 月次取引量 500 万 USDT → VIP1 到達目標を設計（maker 0.015%）

### テスト追加チェックリスト

- [ ] `tests/test_min_half_spread.py`：MIN_HALF_SPREAD_BPS を割るクォートが出ないことを検証
- [ ] `tests/test_oms_lock.py`：flatten と place_quote の並走で二重発注が起きないことを検証
- [ ] `tests/test_tfi.py`：TFI 集計のウィンドウ境界・正規化を検証
- [ ] `tests/test_pnl_logger.py`：1 分タイマーと 5 項目の計算ロジックを検証
- [ ] `tests/test_adverse_guards.py`：3 点 adverse selection ガードの閾値を検証
