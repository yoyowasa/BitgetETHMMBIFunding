# STATUS — Bitget ETHUSDT MM Bot

最終更新: 2026-04-24

---

## 現在の状態

**フェーズ**: 設計完了・実装着手前（DRY_RUN=0 未実施）

**最重要課題**: 現行パラメータは構造的赤字（EV/往復 = -6〜-10bps）。Phase A を完了するまで DRY_RUN=0 不可。

---

## 実装状態サマリ

| コンポーネント | 状態 | 備考 |
|---|---|---|
| WS books5 購読（SPOT/PERP） | ✅ 実装済 | bitget_gateway.py |
| WS private fill/orders/positions | ✅ 実装済 | bitget_gateway.py |
| WS trade チャンネル購読 | ✅ 実装済 | bitget_gateway.py |
| posMode 起動時検査 | ✅ 実装済 | AUTO_SET_POS_MODE 対応 |
| constraints ロード | ✅ 実装済 | exchange/constraints.py |
| PERP timeInForceValue 統一 | ✅ 実装済 | bitget_gateway.py |
| QUOTE（post_only 両建て） | ✅ 実装済 | mm_funding.py |
| MIN_HALF_SPREAD_BPS 下限ガード | ✅ 実装済 | mm_funding.py / config.py |
| micro_price（板厚加重 mid） | ✅ 実装済 | mm_funding.py |
| TFI 集計モジュール | ✅ 実装済 | bot/marketdata/tfi.py |
| adverse selection ガード 3 点 | ✅ 実装済 | guards.py / mm_funding.py |
| symbol 単位 asyncio.Lock | ✅ 実装済 | oms.py |
| HedgeTicket（部分約定追跡） | ✅ 実装済 | oms.py |
| fill dedupe（instType+tradeId） | ✅ 実装済 | oms.py |
| reprice 抑制（threshold） | ✅ 実装済 | oms.py |
| book_stale / funding_stale ガード | ✅ 実装済 | mm_funding.py |
| unhedged_exposure ガード | ✅ 実装済 | oms.py |
| max_inventory_notional ガード | ✅ 実装済 | mm_funding.py |
| reject_streak HALT | ✅ 実装済 | guards.py |
| PnL 分解ロガー（pnl_1min） | ✅ 実装済 | bot/log/pnl_logger.py / app.py |
| SIMULATED_SPOT_FILL 除去 | 部分実装 | 8877d04 で追加、本体統合未確認 |

---

## Phase A チェックリスト（DRY_RUN=0 前に完了必須）

- [x] **A-1** `mm_funding.py` に MIN_HALF_SPREAD_BPS 下限ガード追加
  - `config.yaml` に `strategy.min_half_spread_bps: 8.0` 追加
  - 受入: `tests/test_min_half_spread.py` で spread ≥ 16bps
- [x] **A-2** `config.yaml` の手数料を VIP0 実態値に修正
  - `fee_maker_perp_bps: 2.0`、`fee_taker_spot_bps: 10.0`
  - `base_half_spread_bps: 8.0` に引き上げ
- [x] **A-3** `oms.py` に `symbol_locks` (asyncio.Lock) 追加
  - 全発注パス（place_quote / flatten / cancel）で取得
  - 受入: `tests/test_oms_lock.py` で同一 symbol の flatten/update_quotes 並走を抑止
- [ ] **A-確認** `/api/v2/user/fee` で実際の VIP tier を確認し cost config と照合

## Phase B チェックリスト（A 完了後）

- [x] **B-1** PERP trade チャンネル購読を bitget_gateway.py に追加
- [x] **B-2** adverse selection ガード 3 点（quote_fade / cancel_aggressive / tfi_fade）
- [x] **B-3** `bot/marketdata/tfi.py` 新規作成（5 秒ウィンドウ TFI）

## Phase C チェックリスト（B 完了後）

- [x] **C-1** `bot/log/pnl_logger.py` 新規作成 + app.py に 1 分タスク追加
- [x] **C-2** `oms.py` に reprice_threshold_bps 抑制追加
- [x] **C-3** fill latency（perp → spot ms）を fill ログに追記
- [x] **C-4** quote fill 率・adverse fill 率を記録

## Phase D チェックリスト（C 完了後・PnL 数値確認後に着手）

- [x] **D-1** micro_price（板厚加重 mid）を予約価格土台に
- [x] **D-2** funding_skew_bps 実装
- [x] **D-3** target_perp_inventory（Funding 受取方向在庫目標）
- [x] **D-4** ヘッジラダー化（post_only → IOC フォールバック）
- [x] **D-5** Funding ウィンドウ戦略
- [x] **D-6** micro_price + TFI を予約価格に統合（A-S 簡易版）

## Phase E — 運用タスク（コード不要）

- [ ] **E-1** BGB 保有 → 手数料 20% 割引（有効後 min_half_spread_bps=7.0 に下げ可）
- [ ] **E-2** Bitget MM プログラムにメール申請
- [ ] **E-3** 月次出来高 500 万 USDT → VIP1 目標設計

---

## テスト追加チェックリスト

- [x] `tests/test_min_half_spread.py`
- [x] `tests/test_oms_lock.py`
- [x] `tests/test_tfi.py`
- [x] `tests/test_pnl_logger.py`
- [x] `tests/test_adverse_guards.py`

- [x] `tests/test_reprice_threshold.py`
- [x] `tests/test_phase_d_strategy.py`
- [x] `tests/test_hedge_ladder.py`

---

## 2026-04-24 実装履歴

### 観測事実
- `bot/strategy/mm_funding.py` に half spread 下限ガードが無かった。
- `bot/oms/oms.py` は同一 symbol の quote / cancel / flatten 排他が無かった。
- `config.yaml` は VIP0 より楽観的な cost 前提だった。
- `AGENTS.md` が無く、`AGENT.md` のみ存在した。

### 実装
- `bot/config.py` に `StrategyConfig.min_half_spread_bps` を追加。
- `bot/strategy/mm_funding.py` に `spread_below_min` ログ付き下限ガードを追加。
- `bot/oms/oms.py` に `symbol_locks` と quote/cancel/flatten 排他を追加。
- `config.yaml` を VIP0 前提へ更新。
- `tests/test_min_half_spread.py`、`tests/test_oms_lock.py` を追加。
- `AGENTS.md` を新規作成。

### 未解決
- `/api/v2/user/fee` による実 tier 照合は未実施。
- Phase C 以降の PnL logger / reprice / fill latency / adverse fill 率は未着手。

## 2026-04-24 Phase B 実装履歴

### 観測事実
- public WS は `trade` を購読していなかった。
- strategy tick に `tfi` が無く、adverse selection 判定材料が無かった。
- trade row の field 名は仕様未確定なので複数候補吸収が必要だった。

### 実装
- `bitget_gateway.py` に PERP `trade` 購読、trade 正規化、TFI 集計、直近 trade / mid 履歴を追加。
- `bot/marketdata/tfi.py` を新規作成。
- `bot/risk/guards.py` に 3 guard 関数を追加。
- `bot/strategy/mm_funding.py` に `quote_fade` / `cancel_aggressive` / `tfi_fade` を接続し、`tick` ログへ `tfi` を追加。
- `tests/test_tfi.py`、`tests/test_adverse_guards.py` を追加。

### 未解決
- Bitget `trade` チャンネルの実 field 名は live WS で再確認が必要。

## 2026-04-24 Phase C 実装履歴

### 観測事実
- `pnl_1min` 出力タスクが無かった。
- OMS は quote 再配置の bps しきい値を持っていなかった。
- fill ログに `hedge_latency_ms` が無く、quote fill率 / adverse fill率も集計されていなかった。

### 実装
- `bot/log/pnl_logger.py` を新規作成し、`gross_spread / fees / funding / hedge_slip / basis / net_pnl` を 1 分 flush する集計器を追加。
- `bot/app.py` に `pnl.jsonl` と 60 秒タスクを追加。
- `bot/oms/oms.py` に `reprice_threshold_bps`、`hedge_latency_ms`、quote fill率 / adverse fill率の集計を追加。
- `config.py` / `config.yaml` に `reprice_threshold_bps`、`adverse_fill_horizon_sec` を追加。
- `tests/test_pnl_logger.py`、`tests/test_reprice_threshold.py` を追加。

### 未解決
- `basis_pnl` は現状 1 分 flush 時点の `perp_mid - spot_mid` と `perp_pos` から簡易計算。
- `adverse_fill_horizon_sec` は設定追加のみで、現状の adverse 判定は fill 時点 mid 比較。

## 2026-04-24 Phase D 実装履歴

### 観測事実
- 予約価格は `mid_perp` 基準で、`micro_price` と `TFI` を取り込んでいなかった。
- Funding 受取方向の在庫目標と funding window の傾け処理が無かった。
- hedge は常に IOC 相当で、post_only 待機フェーズが無かった。

### 実装
- `mm_funding.py` の予約価格を `micro_price + OBI + TFI - inventory_penalty` に変更。
- `funding_skew_bps_per_rate`、`target_inventory_max_ratio`、`funding_window_sec` を追加し、funding 方向在庫目標を実装。
- `oms.py` に簡易ヘッジラダーを追加し、未ヘッジ猶予前半は `post_only`、後半は `IOC` に切替。
- `tests/test_phase_d_strategy.py`、`tests/test_hedge_ladder.py` を追加。

### 未解決
- hedge ラダーは簡易版で、実 impact 見積りは未実装。

## 2026-04-24 audit 修正履歴

### 観測事実
- `tools/audit.ps1` は `.venv\Scripts\python.exe` 固定で、GitHub Actions の `setup-python` 環境では失敗した。
- `.github\workflows\audit.yml` は `python` を導入済みだった。

### 実装
- `tools/audit.ps1` に Python 解決ロジックを追加。
- 優先順を `venv` → `python` → `py -3` に変更。
- audit 開始時に使用 Python と source を出力するようにした。

### 未解決
- CI 上での再実行結果は未確認。

## 2026-04-24 audit 依存修正履歴

### 観測事実
- `.github\workflows\audit.yml` は `requirements-dev.txt` のみをインストールしていた。
- `requirements-dev.txt` に `PyYAML`、`pybotters` が無く、pytest collection が失敗した。

### 実装
- `requirements-dev.txt` の先頭に `-r requirements.txt` を追加。

### 未解決
- GitHub Actions 上での再実行結果は未確認。

---

## 直近コミット（参考）

```
42842f9  chore: add audit baseline and fix lint import
8877d04  feat: add near-live fill simulation and pnl validation
16b5b9c  chore: venv311を追跡対象から除外
ec1b00a  chore: bulk update after lint & format
```

## 既知の問題・未確定点

| 項目 | 内容 |
|---|---|
| 実際の VIP tier | 確認未済。cost config が実態と乖離している可能性が高い |
| basis_pnl 計算式 | 「保有ポジ × 乖離変化」か「累積乖離」かを決定していない |
| trade チャンネルのフィールド名 | Bitget WS ドキュメントで要確認（side フィールド名等） |
| max_unhedged_sec 2.0 秒 | 攻めすぎの可能性。5.0 秒への緩和を検討中 |
| 8877d04 の fill simulation | 本体への統合が未確認。`simulated:true` ログが残っているか要確認 |
