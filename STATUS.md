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
| 総合EV判定（spread+funding-cost） | ✅ 実装済 | mm_funding.py |

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
- [x] **A-確認** VIP0 / BGB割引なし前提で cost config と照合
  - 前提: VIP0、BGB割引なし
  - 照合結果: `fee_maker_perp_bps: 2.0`、`fee_taker_spot_bps: 10.0` を維持

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
- [x] `tests/test_total_edge.py`

## 2026-04-24 総合EV修正履歴

### 観測事実
- 旧ロジックは `expected_funding - expected_cost` で Funding単体判定だった。
- ETH Funding 水準では構造的に `edge_negative` へ寄りやすかった。

### 実装
- `mm_funding.py` の Funding単体 `edge_negative` を削除。
- `expected_spread_bps + funding_bps - cost_bps - adverse_buffer_bps` の総合EV判定へ置換。
- `config.yaml` / `config.py` に `strategy.adverse_buffer_bps` を追加。
- `reason=edge_negative_total` の risk ログへ詳細項目を追加。
- `tests/test_total_edge.py` を追加。

### 未解決
- 総合EVは両側約定前提の簡易期待値。

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

## 2026-04-24 A-確認更新履歴

### 観測事実
- 運用前提は `VIP0 / BGB割引なし` で固定。
- `config.yaml` の cost は `fee_maker_perp_bps: 2.0`、`fee_taker_spot_bps: 10.0`。

### 実装
- A-確認を「VIP0 / BGB割引なし前提で照合済み」に更新。

### 未解決
- `/api/v2/user/fee` による API 実測は未実施。
- live で `slippage_bps: 2.0` に収まるかは未確認。

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

## 2026-04-24 停止理由集計スクリプト追加履歴

### 観測事実
- 停止理由は `decision.jsonl` の `event=risk` に出ているが、件数比較しづらかった。
- `reports` ディレクトリは未作成だった。

### 実装
- `scripts\analyze_stop_reasons.py` を追加。
- `logs\*.jsonl` の `event=risk` を集計し、`reports\stop_reason_counts.csv` と `reports\edge_negative_details.csv` を出力するようにした。
- `reports` はスクリプト内で自動作成するようにした。

### 未解決
- 集計対象は `logs\*.jsonl` のみで、サブディレクトリ配下は含めていない。

## 2026-04-24 guard 発火分析スクリプト追加履歴

### 観測事実
- `cancel_aggressive` / `quote_fade` / `tfi_fade` の件数だけでは、市場状態との対応が見えなかった。
- 実ログには `mid_perp` / `mid_100ms_ago` / `tfi` / `trade_px` / `trade_side` が入っていた。

### 実装
- `scripts\analyze_guard_triggers.py` を追加。
- `logs\*.jsonl` の `event=risk` から guard 発火だけを抽出し、`reports\guard_trigger_details.csv` を出力するようにした。
- `mid` / `mid_prev` / `mid_move_bps` はログ実態に合わせて `mid_perp` / `mid_100ms_ago` から補完するようにした。

### 未解決
- `bid_px` / `ask_px` / `spread_bps` は現行 risk ログに無いケースが多く、空列が残る可能性がある。

## 2026-04-24 guard forward return 分析スクリプト追加履歴

### 観測事実
- guard 発火回数だけでは、発火後に本当に危険方向へ進んだか判断できなかった。
- `decision.jsonl` の `tick` には `mid_perp` が継続記録されていた。

### 実装
- `scripts\analyze_guard_forward_returns.py` を追加。
- `logs\*.jsonl` から `tick` の `mid_perp` 時系列と guard 発火を抽出し、1秒後 / 3秒後 / 5秒後の mid return を `reports\guard_forward_returns.csv` に出力するようにした。
- `mid_move_bps` は既存値を優先し、無ければ `mid_perp` と `mid_100ms_ago` から補完するようにした。

### 未解決
- 発火時 `mid_at_trigger` が空の行は return が空欄になる。

## 2026-04-24 cancel_aggressive ログ拡張履歴

### 観測事実
- `cancel_aggressive` の risk ログには `trade_px` / `trade_side` / `tfi` はあったが、`mid_perp` が無かった。
- そのため `guard_forward_returns.csv` で `cancel_aggressive` の return が空欄になっていた。

### 実装
- `mm_funding.py` の `reason=cancel_aggressive` ログに `mid_perp`、`bid_px`、`ask_px`、`spread_bps` を追加した。
- 判定条件や閾値は変更していない。

### 未解決
- 実ログを再生成して、`cancel_aggressive` の return 列が埋まることを再確認する必要がある。

## 2026-04-24 guard forward summary 集計スクリプト追加履歴

### 観測事実
- `guard_forward_returns.csv` は行単位で、統計判断には再集計が必要だった。
- `trade_side` は `cancel_aggressive` では埋まるが、`quote_fade` では空欄が基本だった。

### 実装
- `scripts\analyze_guard_forward_summary.py` を追加。
- `guard_forward_returns.csv` を読み、`reason` 別と `reason + trade_side` 別に `ret_1s_bps` / `ret_3s_bps` / `ret_5s_bps` の
  件数・平均・中央値・プラス比率・マイナス比率を `reports\guard_forward_summary.csv` へ出力するようにした。

### 未解決
- `trade_side` 空欄グループは `quote_fade` などで残る。

## 2026-04-24 directional success 分析スクリプト追加履歴

### 観測事実
- `guard_forward_summary.csv` は return の正負集計で、guard が想定した危険方向との一致率は直接見られなかった。
- `guard_forward_returns.csv` には `trade_side`、`mid_move_bps`、`tfi` があり、guard 別の方向正規化に使える。

### 実装
- `scripts\analyze_guard_directional_success.py` を追加。
- `cancel_aggressive` は `trade_side`、`quote_fade` は `mid_move_bps`、`tfi_fade` は `tfi` で危険方向を定義し、`reports\guard_directional_success.csv` へ集計するようにした。
- 出力は `reason` 別と `reason + trade_side` 別で、`success_ratio` / `fail_ratio` / `neutral_ratio` / `mean_directional_ret_bps` を含む。
- 最小検証: `.\.venv\Scripts\python.exe scripts\analyze_guard_directional_success.py` が `done` で完了。
- 代表結果: `cancel_aggressive sell ret_5s_bps success_ratio=0.2312 fail_ratio=0.2258 neutral_ratio=0.5430 mean_directional_ret_bps=0.0253`。
- 代表結果: `quote_fade ret_3s_bps success_ratio=0.3962 fail_ratio=0.1698 neutral_ratio=0.4340 mean_directional_ret_bps=3.5908`。

### 未解決
- guard 閾値・spread 設定は変更していない。

## 2026-04-24 直近ログ限定 directional success 分析スクリプト追加履歴

### 観測事実
- 旧ログには `cancel_aggressive` の `mid_at_trigger` が空の行が混在していた。
- 旧ログ混在により、`cancel_aggressive` の neutral が膨らみやすかった。

### 実装
- `scripts\analyze_guard_directional_success_recent.py` を追加。
- `--start-ts` 指定時はその時刻以降、未指定時は `mid_at_trigger` が空でない行だけを対象にするようにした。
- 出力CSVは `reports\guard_directional_success_recent.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_guard_directional_success_recent.py`
- 最小検証: `done` で完了し、`reports\guard_directional_success_recent.csv` を生成。
- 代表結果: `cancel_aggressive sell ret_5s_bps success_ratio=0.4674 fail_ratio=0.4565 neutral_ratio=0.0761 mean_directional_ret_bps=0.0253`。
- 代表結果: `quote_fade ret_3s_bps success_ratio=0.3962 fail_ratio=0.1698 neutral_ratio=0.4340 mean_directional_ret_bps=3.5908`。

### 未解決
- guard 閾値・spread 設定は変更していない。

## 2026-04-24 guard overlap 分析スクリプト追加履歴

### 観測事実
- `cancel_aggressive` が単独で有効か、`quote_fade` と同じ危険局面を重複検知しているかは未分解だった。
- `guard_forward_returns.csv` には guard trigger の `ts`、`reason`、forward return が揃っている。

### 実装
- `scripts\analyze_guard_overlap.py` を追加。
- 1秒以内に別 reason の guard が発火した場合を overlap として分類するようにした。
- 出力CSVは `reports\guard_overlap_details.csv` と `reports\guard_overlap_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_guard_overlap.py`
- 最小検証: `done` で完了し、details 334 行、summary 15 行を生成。
- 代表結果: `single_cancel_aggressive cancel_aggressive sell ret_5s_bps count=58 directional_success_ratio=0.4828 directional_fail_ratio=0.5172 mean_directional_ret_bps=-0.0448`。
- 代表結果: `overlap_quote_fade_cancel_aggressive cancel_aggressive sell ret_5s_bps count=30 directional_success_ratio=0.5000 directional_fail_ratio=0.4000 mean_directional_ret_bps=0.1760`。
- 代表結果: `overlap_quote_fade_cancel_aggressive quote_fade ret_3s_bps count=49 directional_success_ratio=0.4286 directional_fail_ratio=0.1837 mean_directional_ret_bps=3.5908`。

### 未解決
- guard 閾値・spread 設定は変更していない。

## 2026-04-24 cancel_aggressive policy simulation 追加履歴

### 観測事実
- `single_cancel_aggressive` は overlap 時より弱く、単独発火を抑制する案を実ロジック変更前に比較する必要があった。
- `guard_overlap_details.csv` には policy 判定に必要な `overlap_group` と `tfi` が含まれている。

### 実装
- `scripts\simulate_cancel_aggressive_policies.py` を追加。
- `A_current`、`B_overlap_quote_fade_only`、`C_overlap_or_strong_tfi` の enabled true/false を CSV 上で比較できるようにした。
- 出力CSVは `reports\cancel_aggressive_policy_sim_details.csv` と `reports\cancel_aggressive_policy_sim_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\simulate_cancel_aggressive_policies.py`
- 最小検証: `done` で完了し、summary 15 行を生成。A/B/C と enabled true/false を確認。
- 代表結果: `A_current true ret_5s_bps count=281 success_ratio=0.2740 fail_ratio=0.2100 mean_directional_ret_bps=0.0399`。
- 代表結果: `B_overlap_quote_fade_only true ret_5s_bps count=172 success_ratio=0.2616 fail_ratio=0.1221 mean_directional_ret_bps=0.1723`。
- 代表結果: `C_overlap_or_strong_tfi true ret_5s_bps count=208 success_ratio=0.2500 fail_ratio=0.1250 mean_directional_ret_bps=0.1375`。

### 未解決
- 実ロジック変更、guard 閾値変更、spread 設定変更はしていない。

## 2026-04-24 cancel_aggressive_policy 実装履歴

### 観測事実
- `cancel_aggressive` は判定後すぐ `cancel_all` しており、policy 候補を DRY_RUN で切り替えて検証できなかった。
- policy simulation では `C_overlap_or_strong_tfi` が候補になった。

### 実装
- `StrategyConfig.cancel_aggressive_policy` を追加。デフォルトは `current`。
- `config.yaml` に `strategy.cancel_aggressive_policy: current` を追加し、本番相当の既定挙動を維持。
- `mm_funding.py` に policy filter を追加。
- `current` は現行通り有効、`overlap_quote_fade_only` は直近1秒以内の `quote_fade` 時のみ有効、`overlap_or_strong_tfi` は直近1秒以内の `quote_fade` または `abs(tfi) >= 0.7` の時のみ有効。
- policy 抑制時は `reason=cancel_aggressive_suppressed`、`policy_enabled=false`、`last_quote_fade_age_ms` などを risk ログに出すようにした。

### 検証
- `.\.venv\Scripts\python.exe -m pytest`: 17 passed。
- `.\.venv\Scripts\python.exe -m compileall bot`: 成功。
- `.\.venv\Scripts\python.exe -m ruff check bot\strategy\mm_funding.py bot\config.py scripts\simulate_cancel_aggressive_policies.py`: All checks passed。
- 一時 config で `cancel_aggressive_policy=overlap_or_strong_tfi` にして `DRY_RUN=1` 60秒起動。
- DRY_RUN結果: 直近 policy ログ 141 件、`policy_enabled=true` 107 件、`cancel_aggressive_suppressed / policy_enabled=false` 34 件を確認。

### 未解決
- `overlap_or_strong_tfi` は検証候補であり、まだ本番採用していない。
- guard 閾値・spread 設定・DRY_RUN=0 は変更していない。

## 2026-04-24 suppressed cancel forward return 分析追加履歴

### 観測事実
- `cancel_aggressive_policy=overlap_or_strong_tfi` の DRY_RUN 検証ログに `cancel_aggressive_suppressed` が 34 件あった。
- 抑制後に危険方向へ進んだかは未集計だった。

### 実装
- `scripts\analyze_suppressed_cancel_forward_returns.py` を追加。
- `logs\*.jsonl` の `event=tick` から `mid_perp` 時系列を作り、`reason=cancel_aggressive_suppressed` の 1秒/3秒/5秒 forward return を計算するようにした。
- `trade_side` に応じて危険方向を正規化した `directional_ret` と `safe_suppression` / `unsafe_suppression` / `neutral` を出力するようにした。
- 出力CSVは `reports\suppressed_cancel_forward_returns.csv` と `reports\suppressed_cancel_forward_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_suppressed_cancel_forward_returns.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\analyze_suppressed_cancel_forward_returns.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_suppressed_cancel_forward_returns.py`: All checks passed。
- details 34 行、summary 6 行を生成。
- `ret_1s_bps` / `ret_3s_bps` / `ret_5s_bps` は各 34 件で空欄ばかりではない。
- 代表結果: `overlap_or_strong_tfi buy ret_3s count=5 safe_ratio=0.0000 unsafe_ratio=0.2000 neutral_ratio=0.8000 mean_directional_ret_bps=0.0693`。
- 代表結果: `overlap_or_strong_tfi sell ret_5s count=29 safe_ratio=0.0000 unsafe_ratio=0.0690 neutral_ratio=0.9310 mean_directional_ret_bps=0.0762`。

### 未解決
- neutral 比率が高く、短期の判断材料はまだ薄い。
- 実ロジック変更、guard 閾値変更、spread 設定変更、DRY_RUN=0 はしていない。

## 2026-04-24 overlap_or_strong_tfi DRY_RUN 追加検証ログ収集

### 観測事実
- 検証前の `cancel_aggressive_suppressed` は 34 件で、本番採用判断には不足していた。
- `config.yaml` は検証前に `dry_run: true`、`cancel_aggressive_policy: current`、`base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0` だった。

### 検証
- 一時的に `strategy.cancel_aggressive_policy: overlap_or_strong_tfi` へ変更し、`DRY_RUN=1` で 10 分起動。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 600`
- 起動ログで `env_DRY_RUN=1` / `dry_run=True` を確認。
- 10 分後の `cancel_aggressive_suppressed` は 274 件。
- `.\.venv\Scripts\python.exe scripts\analyze_suppressed_cancel_forward_returns.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_suppressed_cancel_forward_returns.py`: All checks passed。
- `reports\suppressed_cancel_forward_returns.csv`: 274 行。
- `reports\suppressed_cancel_forward_summary.csv`: 6 行。
- `ret_1s_bps` / `ret_3s_bps` / `ret_5s_bps` は各 274 件。

### 代表結果
- `buy ret_1s count=105 unsafe_ratio=0.1048 neutral_ratio=0.8952 mean_directional_ret_bps=0.0807`
- `buy ret_5s count=105 safe_ratio=0.1429 unsafe_ratio=0.2762 neutral_ratio=0.5810 mean_directional_ret_bps=-0.0082`
- `sell ret_1s count=169 unsafe_ratio=0.1361 neutral_ratio=0.8225 mean_directional_ret_bps=0.1233`
- `sell ret_5s count=169 safe_ratio=0.1657 unsafe_ratio=0.3550 neutral_ratio=0.4793 mean_directional_ret_bps=0.1666`

### 未解決
- 検証後に `config.yaml` の `strategy.cancel_aggressive_policy` は `current` へ戻した。
- neutral 比率が高く、今回だけでは本番採用判断はしない。
- 実ロジック変更、guard 閾値変更、spread 設定変更、DRY_RUN=0 はしていない。

## 2026-04-24 overlap_quote_fade_only DRY_RUN 追加検証ログ収集

### 観測事実
- 前回の `overlap_or_strong_tfi` は `sell ret_5s unsafe_ratio=0.3550`、`mean_directional_ret_bps=0.1666` で、抑制しすぎ疑いがあった。
- 検証前の `overlap_quote_fade_only` の `cancel_aggressive_suppressed` は 0 件だった。
- `config.yaml` は検証前に `dry_run: true`、`cancel_aggressive_policy: current`、`base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0` だった。

### 検証
- 一時的に `strategy.cancel_aggressive_policy: overlap_quote_fade_only` へ変更し、`DRY_RUN=1` で 10 分起動。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 600`
- 起動ログで `env_DRY_RUN=1` / `dry_run=True` を確認。
- 10 分後の `overlap_quote_fade_only` の `cancel_aggressive_suppressed` は 1075 件。
- `.\.venv\Scripts\python.exe scripts\analyze_suppressed_cancel_forward_returns.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_suppressed_cancel_forward_returns.py`: All checks passed。
- `reports\suppressed_cancel_forward_returns.csv`: 1349 行。内訳は `overlap_or_strong_tfi` 274 件、`overlap_quote_fade_only` 1075 件。
- `reports\suppressed_cancel_forward_summary.csv`: 12 行。
- `overlap_quote_fade_only` の `ret_1s_bps` / `ret_3s_bps` / `ret_5s_bps` は各 1075 件。

### 代表結果
- `overlap_quote_fade_only buy ret_5s count=454 safe_ratio=0.3877 unsafe_ratio=0.2841 neutral_ratio=0.3282 mean_directional_ret_bps=0.1017`
- `overlap_quote_fade_only sell ret_5s count=621 safe_ratio=0.2206 unsafe_ratio=0.4106 neutral_ratio=0.3688 mean_directional_ret_bps=0.0641`

### 前回比較
- `overlap_or_strong_tfi buy ret_5s`: `count=105 unsafe_ratio=0.2762 mean_directional_ret_bps=-0.0082`
- `overlap_quote_fade_only buy ret_5s`: `count=454 unsafe_ratio=0.2841 mean_directional_ret_bps=0.1017`
- `overlap_or_strong_tfi sell ret_5s`: `count=169 unsafe_ratio=0.3550 mean_directional_ret_bps=0.1666`
- `overlap_quote_fade_only sell ret_5s`: `count=621 unsafe_ratio=0.4106 mean_directional_ret_bps=0.0641`

### 未解決
- 検証後に `config.yaml` の `strategy.cancel_aggressive_policy` は `current` へ戻し、`dry_run: true` も確認した。
- `overlap_quote_fade_only` は `sell ret_5s unsafe_ratio` が前回より高く、単純採用は保留。
- 実ロジック変更、guard 閾値変更、spread 設定変更、DRY_RUN=0 はしていない。

## 2026-04-24 cancel_aggressive_policy 最終判断記録

### 観測事実
- `config.yaml` は `strategy.cancel_aggressive_policy: current`、`dry_run: true`、`base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0`。
- `overlap_quote_fade_only` は suppressed 1075 件を収集済み。
- `overlap_or_strong_tfi` は suppressed 274 件を収集済み。
- 判断記録を `reports\cancel_aggressive_policy_decision.md` に作成。

### 判断
- `cancel_aggressive_policy=current` を維持する。
- `overlap_quote_fade_only` は本番採用しない。
- `overlap_or_strong_tfi` は本番採用しない。
- `DRY_RUN=0` は不可。
- guard 閾値と spread 設定は変更しない。

### 根拠
- `overlap_quote_fade_only sell ret_5s`: `count=621 unsafe_ratio=0.4106 mean_directional_ret_bps=0.0641`。
- `overlap_quote_fade_only buy ret_5s`: `count=454 unsafe_ratio=0.2841 mean_directional_ret_bps=0.1017`。
- `overlap_or_strong_tfi sell ret_5s`: `count=169 unsafe_ratio=0.3550 mean_directional_ret_bps=0.1666`。
- `overlap_or_strong_tfi buy ret_5s`: `count=105 unsafe_ratio=0.2762 mean_directional_ret_bps=-0.0082`。
- `cancel_aggressive` 抑制後、特に sell 側で危険方向へ進むケースが多く、抑制しすぎ疑いが残る。

### 次の焦点
- policy filtering の追加検証はいったん停止する。
- 次は `edge_negative_total`、spread 不足、`quote_fade` 継続評価を優先する。

### 未解決
- 実ロジック変更、guard 閾値変更、spread 設定変更、DRY_RUN=0 はしていない。

## 2026-04-24 spread EV scenario 分析追加履歴

### 観測事実
- `edge_negative_total` の risk ログは 1386 件あった。
- ログ上の代表値は `cost_bps=28.0`、`adverse_buffer_bps=2.0`、`funding_bps` はおおむね `0.39〜0.60`。
- `config.yaml` は `base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0`、`cancel_aggressive_policy: current`、`dry_run: true` のまま。

### 実装
- `scripts\analyze_spread_ev_scenarios.py` を追加。
- `logs\*.jsonl` の `event=risk` / `reason=edge_negative_total` を読み、`required_half_bps=(cost_bps+adverse_buffer_bps-funding_bps)/2` を計算。
- half spread シナリオ `8.0 / 10.0 / 12.0 / 14.0 / 15.0 / 16.0 / 18.0 / 20.0` の仮想EVを出力するようにした。
- 出力CSVは `reports\spread_ev_scenarios.csv` と `reports\spread_ev_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_spread_ev_scenarios.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\analyze_spread_ev_scenarios.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_spread_ev_scenarios.py`: All checks passed。
- `reports\spread_ev_scenarios.csv`: 11088 行。
- `reports\spread_ev_summary.csv`: 8 行。

### 代表結果
- `scenario_half_bps=8.0`: `count=1386 pass_ratio=0.0000 mean_edge_bps=-13.4220`
- `scenario_half_bps=15.0`: `count=1386 pass_ratio=1.0000 mean_edge_bps=0.5780`
- `scenario_half_bps=18.0`: `count=1386 pass_ratio=1.0000 mean_edge_bps=6.5780`
- `required_half_bps`: `mean=14.7110 median=14.7100 p75=14.7150 p90=14.7200`

### 推論
- 現行 `half_spread_bps=8.0` では総合EV上 quote が出にくい。
- `15.0` 付近で理論EVはプラス化するが、損益分岐に近く本番採用判断には約定品質評価が必要。

### 未解決
- spread 設定変更、guard 閾値変更、実ロジック変更、DRY_RUN=0 はしていない。

## 2026-04-24 15bps / 18bps spread DRY_RUN 比較履歴

### 観測事実
- `15bps` と `18bps` をそれぞれ `DRY_RUN=1` で約 10 分検証した。
- 共通設定は `cancel_aggressive_policy: current`、`dry_run: true`。
- 起動ログで両方とも `env_DRY_RUN=1` / `dry_run=True` を確認。
- 検証後、`config.yaml` は `base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0`、`cancel_aggressive_policy: current`、`dry_run: true` に戻した。

### 保存先
- `reports\spread_dryrun_compare\15bps\`
- `reports\spread_dryrun_compare\18bps\`
- 比較サマリ: `reports\spread_dryrun_compare\spread_dryrun_compare_summary.md`

### 15bps 代表値
- 実行時間: 約 10 分。
- `order_new` quote 件数: 28。
- `edge_negative_total`: 0。
- `cancel_aggressive`: 1977。
- `quote_fade`: 375。
- `tfi_fade`: 12。
- `order_skip`: 0。
- 推定 `expected_edge_bps` 平均: 0.9990。
- quote は出た。quote action span は約 511 秒。

### 18bps 代表値
- 実行時間: 約 10 分。
- `order_new` quote 件数: 44。
- `edge_negative_total`: 0。
- `cancel_aggressive`: 2160。
- `quote_fade`: 184。
- `tfi_fade`: 9。
- `order_skip`: 0。
- 推定 `expected_edge_bps` 平均: 7.0000。
- quote は出た。quote action span は約 505 秒。

### 推論
- 15bps / 18bps とも `edge_negative_total` は消え、理論EV上の停止は解消した。
- 18bps は 15bps より quote 件数と推定EVが高く、次の検証候補としては 18bps が優勢。
- ただし両方とも guard 発火が多く、特に `cancel_aggressive` が支配的。
- 15bps は損益分岐に近く、本番候補にはしない。

### 未解決
- 今回は本番採用しない。
- DRY_RUN=0、guard 閾値変更、spread 恒久変更、実ロジック変更はしていない。

## 2026-04-24 quote lifecycle 分析追加履歴

### 観測事実
- 15bps / 18bps の保存済み DRY_RUN 比較ログには `order_cancel` / `order_skip` は無く、quote 終了は次の risk guard で近似する必要があった。
- `config.yaml` は `base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0`、`cancel_aggressive_policy: current`、`dry_run: true` のまま。

### 実装
- `scripts\analyze_quote_lifecycle.py` を追加。
- `reports\spread_dryrun_compare\15bps\logs\*.jsonl` と `reports\spread_dryrun_compare\18bps\logs\*.jsonl` から quote lifecycle を集計。
- `order_new` quote 後、最初の `cancel_aggressive` / `quote_fade` / `tfi_fade` / `edge_negative_total` / cancel / skip を終了理由として扱う。
- 出力CSVは `reports\spread_dryrun_compare\quote_lifecycle_details.csv` と `reports\spread_dryrun_compare\quote_lifecycle_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_quote_lifecycle.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\analyze_quote_lifecycle.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_quote_lifecycle.py`: All checks passed。
- 15bps / 18bps の quote_count、mean / median lifetime、終了 guard 件数を確認。

### 代表結果
- `15bps`: `quote_count=28 mean_lifetime_sec=0.2511 median_lifetime_sec=0.2497 p75=0.2604 max=0.2618`
- `15bps`: `cancel_aggressive_end=16 quote_fade_end=2 tfi_fade_end=10`
- `18bps`: `quote_count=44 mean_lifetime_sec=0.2997 median_lifetime_sec=0.2567 p75=0.2636 max=0.7938`
- `18bps`: `cancel_aggressive_end=42 quote_fade_end=2 tfi_fade_end=0`

### 推論
- 18bps は 15bps より quote 件数とEVでは優勢だが、quote lifetime の中央値は約0.257秒で極端に短い。
- 18bps の終了理由はほぼ `cancel_aggressive` で、quote は出ても guard にすぐ消されている。
- 次の DRY_RUN 候補は 18bps だが、実運用候補としてはまだ弱い。
- 15bps は edge が薄く、本番候補にはしない。

### 未解決
- DRY_RUN=0、spread 恒久変更、guard 閾値変更、実ロジック変更はしていない。

## 2026-04-24 cancel_aggressive density 分析追加履歴

### 観測事実
- 18bps でも quote 寿命中央値が約 0.257 秒と短く、終了理由はほぼ `cancel_aggressive` だった。
- `config.yaml` は `base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0`、`cancel_aggressive_policy: current`、`dry_run: true` のまま。

### 実装
- `scripts\analyze_cancel_aggressive_density.py` を追加。
- `reports\spread_dryrun_compare\quote_lifecycle_details.csv` の quote 区間を使い、`cancel_aggressive` を quote中 / 非quote中 に分けて密度集計。
- 出力CSVは `reports\spread_dryrun_compare\cancel_aggressive_density_details.csv` と `reports\spread_dryrun_compare\cancel_aggressive_density_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_cancel_aggressive_density.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\analyze_cancel_aggressive_density.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_cancel_aggressive_density.py`: All checks passed。
- 15bps / 18bps の quote中 / 非quote中、trade_side 別の `events_per_sec`、`tfi`、`spread_bps` を確認。

### 代表結果
- `15bps all_quote`: `event_count=8 duration_sec=3.5163 events_per_sec=2.2751`
- `15bps all_non_quote`: `event_count=1969 duration_sec=590.8823 events_per_sec=3.3323`
- `15bps quote中 trade_side`: `buy=0.8532/sec sell=1.4220/sec`
- `18bps all_quote`: `event_count=18 duration_sec=5.5578 events_per_sec=3.2387`
- `18bps all_non_quote`: `event_count=2142 duration_sec=588.9532 events_per_sec=3.6370`
- `18bps quote中 trade_side`: `buy=0.8996/sec sell=2.3390/sec`

### 推論
- `cancel_aggressive` は quote中だけ異常に多いのではなく、市場全体で常時多い。
- 18bps の quote中は `trade_side=sell` 側に偏りがあり、bid quote が売り成行に晒されやすい局面がある。
- spread を広げるだけでは不十分で、次に見るべきは market quality、TFI、片側quote停止、予約価格の方向。
- 18bps は次の DRY_RUN 候補ではあるが、実運用候補としてはまだ弱い。

### 未解決
- DRY_RUN=0、spread 恒久変更、guard 閾値変更、cancel_aggressive_policy 変更、実ロジック変更はしていない。

## 2026-04-24 one-sided quote simulation 追加履歴

### 観測事実
- 18bps DRY_RUN では quote は出るが、quote 寿命中央値は約 0.257 秒で短く、終了理由はほぼ `cancel_aggressive`。
- 18bps の quote中 `cancel_aggressive` は `trade_side=sell` 側に偏りがあり、bid quote が売り成行に晒されやすい局面があった。
- `config.yaml` は `base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0`、`cancel_aggressive_policy: current`、`dry_run: true` のまま。

### 実装
- `scripts\simulate_one_sided_quote.py` を追加。
- `quote_lifecycle_details.csv` と `cancel_aggressive_density_details.csv` を使い、TFI による片側quote停止を CSV 上で仮想適用。
- policy は `A_current`、`B_tfi_0p6`、`C_tfi_0p7`、`D_tfi_0p8`。
- 出力CSVは `reports\spread_dryrun_compare\one_sided_quote_sim_details.csv` と `reports\spread_dryrun_compare\one_sided_quote_sim_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\simulate_one_sided_quote.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\simulate_one_sided_quote.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\simulate_one_sided_quote.py`: All checks passed。
- 15bps / 18bps、A/B/C/D、bid / ask / all の集計を確認。

### 15bps 代表結果
- `A_current all`: `quote_count=28 kept=28 cancel_aggressive_end_kept=16 median_lifetime_kept=0.2497`
- `B_tfi_0p6 all`: `would_suppress_ratio=0.3929 kept=17 cancel_aggressive_end_kept=11 median_lifetime_kept=0.2496`
- `C_tfi_0p7 all`: `would_suppress_ratio=0.2500 kept=21 cancel_aggressive_end_kept=13 median_lifetime_kept=0.2495`
- `D_tfi_0p8 all`: `would_suppress_ratio=0.2500 kept=21 cancel_aggressive_end_kept=13 median_lifetime_kept=0.2495`

### 18bps 代表結果
- `A_current all`: `quote_count=44 kept=44 cancel_aggressive_end_kept=42 median_lifetime_kept=0.2567`
- `B_tfi_0p6 all`: `would_suppress_ratio=0.1818 kept=36 cancel_aggressive_end_kept=34 median_lifetime_kept=0.2580`
- `C_tfi_0p7 all`: `would_suppress_ratio=0.1591 kept=37 cancel_aggressive_end_kept=35 median_lifetime_kept=0.2580`
- `D_tfi_0p8 all`: `would_suppress_ratio=0.1364 kept=38 cancel_aggressive_end_kept=36 median_lifetime_kept=0.2585`
- `18bps C_tfi_0p7 bid`: `would_suppress_ratio=0.0909 kept=20 cancel_aggressive_end_kept=19`
- `18bps C_tfi_0p7 ask`: `would_suppress_ratio=0.2273 kept=17 cancel_aggressive_end_kept=16`

### 推論
- 18bps では片側quote停止により `cancel_aggressive_end_kept` は 42 から 34〜36 へ減るが、quote 寿命中央値の改善は限定的。
- `B_tfi_0p6` は最も多く止めるが、止めすぎ懸念がある。
- `D_tfi_0p8` は保守的だが効果が弱い。
- `C_tfi_0p7` は suppress ratio と kept quote のバランスが比較的よく、次の DRY_RUN 候補。
- ただし今回の結果だけでは本番採用しない。

### 未解決
- DRY_RUN=0、spread 恒久変更、guard 閾値変更、実ロジック変更はしていない。

## 2026-04-24 one_sided_quote_policy 実装・DRY_RUN 検証履歴

### 観測事実
- CSV simulation では `18bps C_tfi_0p7` が suppress ratio と kept quote のバランス上、次の検証候補だった。
- 本番採用は未判断で、まず config 切替式の DRY_RUN 検証が必要だった。

### 実装
- `StrategyConfig.one_sided_quote_policy` を追加。デフォルトは `current`。
- `config.yaml` に `strategy.one_sided_quote_policy: current` を追加し、現行挙動を維持。
- `mm_funding.py` に one-sided quote filter を追加。
- `current` は両側 quote 許可。
- `tfi_0p6` / `tfi_0p7` / `tfi_0p8` は、`tfi <= -threshold` で bid quote を抑制し、`tfi >= threshold` で ask quote を抑制する。
- 抑制時は `reason=one_sided_quote_suppressed`、`one_sided_quote_policy`、`suppressed_leg`、`tfi`、`mid_perp`、`bid_px`、`ask_px`、`spread_bps` を risk ログに出す。
- `tests\test_one_sided_quote_policy.py` を追加。

### テスト
- `.\.venv\Scripts\python.exe -m ruff check bot\strategy\mm_funding.py bot\config.py tests\test_one_sided_quote_policy.py`: All checks passed。
- `.\.venv\Scripts\python.exe -m pytest`: 19 passed。
- `.\.venv\Scripts\python.exe -m compileall bot`: 成功。

### DRY_RUN 検証
- 一時的に `base_half_spread_bps: 18.0`、`min_half_spread_bps: 18.0`、`cancel_aggressive_policy: current`、`one_sided_quote_policy: tfi_0p7`、`dry_run: true` へ変更。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 600`
- 起動ログで `env_DRY_RUN=1` / `dry_run=True` を確認。
- 検証後に `config.yaml` は `base_half_spread_bps: 8.0`、`min_half_spread_bps: 8.0`、`cancel_aggressive_policy: current`、`one_sided_quote_policy: current`、`dry_run: true` へ戻した。

### 18bps + tfi_0p7 代表値
- 実行時間: 約 10 分。
- `one_sided_quote_suppressed`: 20 件。
- `suppressed_leg`: `ask=18`、`bid=2`。
- `order_new quote`: 20 件。
- quote cycle: 20 件。全て片側 quote。
- `cancel_aggressive`: 1794。
- `quote_fade`: 521。
- `tfi_fade`: 20。
- quote lifetime: `mean=0.2546 sec`、`median=0.2539 sec`。
- quote end: `cancel_aggressive=6`、`quote_fade=1`、`tfi_fade=13`。

### 前回 18bps current 比較
- 前回: `quote_count=44`、`median_lifetime=0.2567 sec`、`mean_lifetime=0.2997 sec`、`cancel_aggressive_end=42`。
- 今回: `quote_count=20`、`median_lifetime=0.2539 sec`、`mean_lifetime=0.2546 sec`、`cancel_aggressive_end=6`。
- `cancel_aggressive_end` は減ったが、quote 件数も大きく減り、lifetime 中央値は改善していない。
- 終了理由は `tfi_fade` へ移っており、片側 quote 停止だけでは不十分。

### 判断
- `tfi_0p7` は次の追加検証候補ではあるが、この結果だけでは本番採用しない。
- quote 寿命改善が出ていないため、予約価格・market quality・TFI と quote_fade の関係を追加確認する。

### 未解決
- DRY_RUN=0、spread 恒久変更、guard 閾値変更、本番採用はしていない。

## 2026-04-24 quote placement 分析追加履歴

### 観測事実
- `18bps + tfi_0p7` でも quote 寿命中央値は約 0.25 秒で改善しなかった。
- `order_new` には `mid_perp` / best bid ask が直接無いため、同一 cycle の近傍 `tick` と近傍 risk の `bid_px` / `ask_px` で補完した。
- lifecycle は `reports\spread_dryrun_compare\quote_lifecycle_details.csv` と近い `quote_ts` / `leg` / `price` で結合した。

### 実装
- `scripts\analyze_quote_placement.py` を追加。
- `logs\*.jsonl` の quote order について、mid / micro / best からの距離、best 近辺判定、TFI 方向との整合を計算。
- 出力CSVは `reports\quote_placement_details.csv` と `reports\quote_placement_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_quote_placement.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\analyze_quote_placement.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_quote_placement.py`: All checks passed。
- `order_new` quote は 92 件抽出。
- lifecycle 結合は 72 件。
- `quote_distance_from_mid_bps` / `quote_distance_from_micro_bps` / `quote_distance_from_best_bps`、`aggressive_vs_best`、`directional_alignment` を確認。

### 代表結果
- `aggressive_vs_best`: 全 92 件が `passive_inside_book`。
- `quote_distance_from_best_bps`: `min=10.1784 max=38.7707 mean=20.6397`。
- `directional_alignment`: `against_flow=21`、`neutral=30`、`with_flow=41`。
- `against_flow`: `count=21 median_lifetime=0.2520 sec cancel_aggressive_end=15 tfi_fade_end=5 quote_fade_end=1`。
- `neutral`: `count=30 median_lifetime=0.2550 sec cancel_aggressive_end=28`。
- `with_flow`: `count=41 lifecycle結合あり=21 median_lifetime=0.2511 sec cancel_aggressive_end=15`。

### 推論
- quote が best 近辺に寄りすぎているというより、少なくとも今回ログでは全て best から 10bps 以上離れた passive quote。
- `against_flow` は短寿命で `cancel_aggressive` / `tfi_fade` に消えやすく、TFI 逆行quoteの抑制は引き続き検証価値がある。
- ただし `neutral` でも `cancel_aggressive` が多く、配置だけではなく market quality / trade toxicity 自体が強い。
- micro price からの距離は mid 距離とほぼ同程度で、予約価格だけが極端に毒性側へ寄っている証拠は薄い。

### 未解決
- 実ロジック変更、spread 変更、guard 閾値変更、DRY_RUN=0 はしていない。

## 2026-04-25 market quality filter simulation 追加履歴

### 観測事実
- quote placement 分析では、quote が best 近辺に寄りすぎている証拠は薄かった。
- `neutral` でも `cancel_aggressive` が多く、market quality / trade toxicity 自体が強い可能性があった。

### 実装
- `scripts\simulate_market_quality_filter.py` を追加。
- `reports\quote_placement_details.csv` の各 quote に対し、直近5秒の `cancel_aggressive` 密度、`quote_fade` 件数、`tfi_fade` 件数、guard 合計、`abs(tfi)` を計算。
- A〜H の market quality filter policy を CSV 上で仮想適用した。
- 出力CSVは `reports\market_quality_filter_sim_details.csv` と `reports\market_quality_filter_sim_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\simulate_market_quality_filter.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\simulate_market_quality_filter.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\simulate_market_quality_filter.py`: All checks passed。
- A_current から H_tfi_abs_lte_0p6_and_guard_count_lte_5 まで、bid / ask / all の集計を確認。

### leg=all 代表結果
- `A_current`: `allowed=92 ratio=1.0000 median_lifetime=0.2524 cancel_aggressive_end=58 quote_fade_end=4 tfi_fade_end=10`
- `B_tfi_abs_lte_0p5`: `allowed=22 ratio=0.2391 median_lifetime=0.2580 cancel_aggressive_end_allowed=20 mean_tfi_abs=0.2410 mean_cancel_density=3.4364`
- `C_tfi_abs_lte_0p6`: `allowed=30 ratio=0.3261 median_lifetime=0.2550 cancel_aggressive_end_allowed=28 mean_tfi_abs=0.3230 mean_cancel_density=3.4800`
- `D_cancel_density_lte_1p0`: `allowed=12 ratio=0.1304 median_lifetime=0.2446 cancel_aggressive_end_allowed=2 mean_cancel_density=0.4167`
- `E_cancel_density_lte_2p0`: `allowed=16 ratio=0.1739 median_lifetime=0.2446 cancel_aggressive_end_allowed=2 mean_cancel_density=0.7250`
- `F_guard_count_lte_5`: `allowed=6 ratio=0.0652 median_lifetime=0.2446 cancel_aggressive_end_allowed=2 mean_guard_count=2.0000`
- `G_tfi_abs_lte_0p6_and_cancel_density_lte_2p0`: `allowed=0 ratio=0.0000`
- `H_tfi_abs_lte_0p6_and_guard_count_lte_5`: `allowed=0 ratio=0.0000`

### 推論
- TFI単独フィルタは寿命中央値をわずかに伸ばすが、2倍改善には遠い。
- `C_tfi_abs_lte_0p6` は allowed ratio が 0.3261 で最低限の機会は残るが、`cancel_aggressive_end_allowed=28` で改善は弱い。
- cancel density / guard count 系は `cancel_aggressive_end_allowed` を大きく減らすが、allowed ratio が 0.3 未満で止めすぎ。
- 複合 G/H は許可0件で不採用。
- market quality filter 単独では弱く、実運用候補化には quote設計・toxicity判定・片側停止との組み合わせ検証が必要。

### 未解決
- 有効候補は強いて言えば `C_tfi_abs_lte_0p6` だが、本番採用しない。
- DRY_RUN=0、spread 恒久変更、guard 閾値変更、実ロジック変更はしていない。

## 2026-04-25 combined quote filter simulation 追加履歴

### 観測事実
- 15bps は edge が薄く、本番候補外。
- 18bps は EV 上は候補だが quote 寿命が短い。
- `one_sided_quote_policy=tfi_0p7` は `cancel_aggressive_end` を減らしたが、quote 寿命改善は限定的だった。
- market quality filter 単独も改善が弱かった。

### 実装
- `scripts\simulate_combined_quote_filters.py` を追加。
- `reports\quote_placement_details.csv` から `18bps / current` の quote 44 件だけを対象にした。
- one-sided TFI 0.7 と market quality filter を組み合わせた A〜G policy を CSV 上で仮想適用。
- 出力CSVは `reports\combined_quote_filter_sim_details.csv` と `reports\combined_quote_filter_sim_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\simulate_combined_quote_filters.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\simulate_combined_quote_filters.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\simulate_combined_quote_filters.py`: All checks passed。
- 18bps のみ、A_current から G まで、bid / ask / all の集計を確認。

### leg=all 代表結果
- `A_current`: `allowed=44 ratio=1.0000 median_lifetime=0.2567 cancel_aggressive_end_allowed=42 quote_fade_end_allowed=2 tfi_fade_end_allowed=0`
- `B_one_sided_tfi_0p7`: `allowed=36 ratio=0.8182 median_lifetime=0.2580 cancel_aggressive_end_allowed=34 quote_fade_end_allowed=2`
- `C_market_tfi_abs_lte_0p6`: `allowed=26 ratio=0.5909 median_lifetime=0.2580 cancel_aggressive_end_allowed=24 quote_fade_end_allowed=2`
- `D_one_sided_0p7_plus_tfi_abs_lte_0p6`: `allowed=26 ratio=0.5909 median_lifetime=0.2580 cancel_aggressive_end_allowed=24 quote_fade_end_allowed=2`
- `E_one_sided_0p7_plus_cancel_density_lte_2p0`: `allowed=0 ratio=0.0000`
- `F_one_sided_0p7_plus_guard_count_lte_5`: `allowed=0 ratio=0.0000`
- `G_one_sided_0p7_plus_tfi_abs_lte_0p6_or_cancel_density_lte_2p0`: `allowed=26 ratio=0.5909 median_lifetime=0.2580 cancel_aggressive_end_allowed=24 quote_fade_end_allowed=2`

### 推論
- B は quote 機会を残すが、寿命中央値の改善はほぼ無い。
- C/D/G は allowed ratio 0.5909 で機会は残るが、寿命中央値は約 0.258 秒で 2倍改善には遠い。
- E/F は許可0件で不採用。
- 組み合わせ filter でも quote 寿命の根本改善は見えず、filter 追加だけでは弱い。
- 有効候補は強いて言えば B または C/D/G だが、本番採用候補ではない。

### 未解決
- 次に見るべきは cancel_aggressive の中身、trade proximity、last trade の扱い、または quote refresh / cancel ロジック。
- DRY_RUN=0、spread 恒久変更、guard 閾値変更、実ロジック変更はしていない。

## 2026-04-25 cancel_aggressive quality 分析追加履歴

### 観測事実
- combined quote filter でも quote 寿命が改善せず、`cancel_aggressive` の発火品質を確認する必要があった。
- 初回集計では lifecycle 保存範囲外のログも含まれたため、quote lifecycle の保存期間内に絞って再集計した。

### 実装
- `scripts\analyze_cancel_aggressive_quality.py` を追加。
- `logs\*.jsonl` の `event=risk` / `reason=cancel_aggressive` を抽出。
- `reports\spread_dryrun_compare\quote_lifecycle_details.csv` と結合し、quote中判定、quote価格との距離、best価格との距離、危険方向一致、trade reuse key を集計。
- 出力CSVは `reports\cancel_aggressive_quality_details.csv` と `reports\cancel_aggressive_quality_summary.csv`。
- 実行コマンド: `.\.venv\Scripts\python.exe scripts\analyze_cancel_aggressive_quality.py`

### 検証
- `.\.venv\Scripts\python.exe scripts\analyze_cancel_aggressive_quality.py`: `done`。
- `.\.venv\Scripts\python.exe -m ruff check scripts\analyze_cancel_aggressive_quality.py`: All checks passed。
- details 4125 行を生成。
- in_quote / non_quote、proximity、duplicate、danger direction を確認。

### 代表結果
- `total_cancel_aggressive`: 4125。
- `in_quote_count`: 26。
- `non_quote_count`: 4099。
- `in_quote_ratio`: 0.0063。
- `duplicate_trade_signal`: 0。
- `danger_match_ratio_in_quote`: 0.6154。
- `proximity_to_quote_bps_in_quote`: `median=18.7720 p75=24.5416 p90=33.8524`。
- `proximity_to_best_bps`: `median=0.0000`。
- `quote_leg=bid`: `count=18 danger_match_ratio=0.7222 median_proximity_to_quote_bps=18.8979`。
- `quote_leg=ask`: `count=8 danger_match_ratio=0.3750 median_proximity_to_quote_bps=17.6704`。

### 推論
- `cancel_aggressive` は best 近傍tradeには反応しているが、自分のquoteからは中央値で約18.8bps離れている。
- quote中に発火したものでも danger direction match は 61.5% で、完全には一致していない。
- `non_quote_count` が非常に多く、quote が無い時間にも `cancel_aggressive` 判定が大量に走っている可能性が高い。
- duplicate は 100ms bucket 基準では 0 だったが、同一価格tradeが複数cycleにまたがって使われている疑いは別途確認余地がある。
- 過剰cancelの主因候補は `last_public_trade` の鮮度管理、quote有無確認、または proximity を best ではなく自分のquote基準にしていない点。

### 未解決
- 次に見るべきは `last_public_trade` の timestamp / trade_id 管理、active quote 有無、quote価格基準の proximity 判定。
- DRY_RUN=0、spread 恒久変更、guard 閾値変更、実ロジック変更はしていない。

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
## 2026-04-25 cancel_aggressive 実装精査・診断ログ追加履歴

### 観測事実
- 対象: `bot/strategy/mm_funding.py`, `bot/oms/oms.py`, `bot/exchange/bitget_gateway.py`
- `check_aggressive_trade` には active quote 価格ではなく `perp_bbo.bid` / `perp_bbo.ask` が渡されていた。
- `bot/oms/oms.py` に active quote 読み取り用 `active_quote_snapshot()` を追加した。
- `bot/exchange/bitget_gateway.py` で public trade の `trade_id` を取得できる場合に保持するようにした。
- `bot/strategy/mm_funding.py` に `event=risk`, `reason=cancel_aggressive_diagnostic` を追加した。
- 既存 `reason=cancel_aggressive` / `reason=cancel_aggressive_suppressed` に診断項目を追加した。
- 診断項目: `has_active_quote`, `active_bid_px`, `active_ask_px`, `active_bid_order_id`, `active_ask_order_id`, `active_bid_client_oid`, `active_ask_client_oid`, `best_bid_px`, `best_ask_px`, `trade_px`, `trade_side`, `trade_ts`, `trade_id`, `trade_age_ms`, `used_bid_px`, `used_ask_px`, `used_px_source`, `proximity_to_active_bid_bps`, `proximity_to_active_ask_bps`, `proximity_to_best_bid_bps`, `proximity_to_best_ask_bps`, `proximity_to_active_quote_bps`, `proximity_to_best_bps`

### DRY_RUN=1 120秒確認
- 実行: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 120`
- 起動確認: `env_DRY_RUN=1`, `dry_run=True`
- config確認: `base_half_spread_bps=8.0`, `min_half_spread_bps=8.0`, `cancel_aggressive_policy=current`, `one_sided_quote_policy=current`, `dry_run=true`
- 直近実行範囲: `cancel_aggressive_diagnostic=313`, `cancel_aggressive=313`, `quote_fade=140`
- `has_active_quote=true`: 0
- `has_active_quote=false`: 313
- `used_px_source`: `best_bid_ask=313`
- `trade_id` 行数: 313
- unique `trade_id`: 25
- `trade_age_ms`: mean 9268.2513, median 3002.9192, min 14.7476, max 64914.7079
- `proximity_to_best_bps`: mean 0.0663, median 0.0, min 0.0, max 0.9924
- `proximity_to_active_quote_bps`: count 0

### 推論
- 現行 `cancel_aggressive` は自分の active quote 近傍ではなく、best bid/ask 近傍の public trade を見て発火している。
- DRY_RUN では OMS が active quote を保持しないため、`has_active_quote=false` のまま `cancel_aggressive` が発火する。
- unique `trade_id` が少なく、同じ public trade が複数 tick で再利用されている疑いが強い。
- `trade_age_ms` の中央値が約3秒、最大約65秒で、古い `last_public_trade` の再利用疑いがある。

### 未確定点
- DRY_RUN の active quote 保持仕様により、本番送信時の active quote 有無とは差が出る可能性がある。
- まだ判定条件変更、fresh trade 制限、active quote 近傍判定への変更はしていない。
- `DRY_RUN=0`、spread恒久変更、guard閾値変更、実ロジック変更はしていない。
## 2026-04-25 cancel_aggressive fix candidate simulation 追加履歴

### 観測事実
- 対象: `logs\*.jsonl` の `event=risk`, `reason=cancel_aggressive_diagnostic`
- 追加: `scripts\simulate_cancel_aggressive_fix_candidates.py`
- 出力CSV:
  - `reports\cancel_aggressive_fix_candidate_details.csv`
  - `reports\cancel_aggressive_fix_candidate_summary.csv`
- 比較候補:
  - `A_current`
  - `B_require_active_quote`
  - `C_require_active_quote_and_fresh_trade`
  - `D_require_active_quote_fresh_and_active_proximity`
  - `E_require_active_quote_fresh_active_proximity_and_danger_match`
- 既存120秒診断ログでの初回結果: `rows=313`, `details=1565`
- 初回代表値: `A_current would_trigger_count=313`, `B-E would_trigger_count=0`
- 初回 block 理由: `B-E no_active_quote_count=313`

### 18bps DRY_RUN 追加検証
- B〜E が 0 件だったため、一時的に `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0` に変更して10分収集した。
- 実行: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 600`
- 起動確認: `env_DRY_RUN=1`, `dry_run=True`
- 検証後に `config.yaml` は `base_half_spread_bps=8.0`, `min_half_spread_bps=8.0`, `cancel_aggressive_policy=current`, `one_sided_quote_policy=current`, `dry_run=true` へ戻した。
- 追加後の再集計: `rows=1791`, `details=8955`
- `A_current`: `would_trigger_count=1791`, `would_trigger_ratio=1.0`
- `B_require_active_quote`: `would_trigger_count=0`, `blocked_count=1791`, `no_active_quote_count=1791`
- `C_require_active_quote_and_fresh_trade`: `would_trigger_count=0`, `blocked_count=1791`, `no_active_quote_count=1791`
- `D_require_active_quote_fresh_and_active_proximity`: `would_trigger_count=0`, `blocked_count=1791`, `no_active_quote_count=1791`
- `E_require_active_quote_fresh_active_proximity_and_danger_match`: `would_trigger_count=0`, `blocked_count=1791`, `no_active_quote_count=1791`
- `A_current` の triggered trade age: median 2510.1154ms, p90 11313.8638ms
- `A_current` の `proximity_to_best_bps`: median 0.0, p90 0.2158
- `proximity_to_active_quote_bps` は triggered count 0 のため代表値なし。

### 推論
- `B_require_active_quote` で全件 block されるため、診断ログ上の最大要因は `has_active_quote=false`。
- 18bps DRY_RUN でも `has_active_quote=true` が出ないため、DRY_RUN では quote 発注ログが出ても OMS の active quote として保持されない仕様が影響している。
- `A_current` は best 近傍 trade には反応しているが、trade age は中央値約2.5秒、p90約11.3秒で、古い/再利用 trade の影響が残る。

### 未確定点
- DRY_RUN active quote 非保持のため、C〜E の fresh/proximity/danger 条件の実効比較はまだ不可。
- 次の候補は、実判定変更ではなく、DRY_RUN時にも診断用 active quote snapshot を持てるようにするか、order_new / order_cancel ログから active quote を再構築して再シミュレーションすること。
- まだ実判定変更、spread恒久変更、guard閾値変更、DRY_RUN=0 はしていない。
## 2026-04-25 DRY_RUN virtual active quote registry 追加履歴

### 観測事実
- 対象: `bot/oms/oms.py`, `bot/strategy/mm_funding.py`, `tests/test_dryrun_active_quote_snapshot.py`
- `OMS` に DRY_RUN 専用の virtual active quote registry を追加した。
- `active_quote_snapshot(symbol)` は `has_active_quote`, `active_bid_px`, `active_ask_px`, `active_bid_order_id`, `active_ask_order_id`, `active_bid_client_oid`, `active_ask_client_oid`, `active_bid_qty`, `active_ask_qty`, `active_bid_ts`, `active_ask_ts`, `source` を返す。
- `source` は `live_order` / `dry_run_virtual` / `none`。
- DRY_RUN の valid quote は `_active_quotes` に `source=dry_run_virtual` として保持する。
- DRY_RUN virtual quote は既存の `cancel_all` / replace / size=0 経路で解除される。
- 本番送信時の `_submit_order()` / `place_order()` / `cancel_order()` の挙動は変更していない。
- `cancel_aggressive_diagnostic` に `active_bid_qty`, `active_ask_qty`, `active_bid_ts`, `active_ask_ts`, `active_quote_source` を追加した。

### テスト
- `.\.venv\Scripts\python.exe -m ruff check bot\oms\oms.py bot\strategy\mm_funding.py tests\test_dryrun_active_quote_snapshot.py`: All checks passed。
- `.\.venv\Scripts\python.exe -m pytest tests\test_dryrun_active_quote_snapshot.py`: 3 passed。
- `.\.venv\Scripts\python.exe -m pytest`: 22 passed。
- `.\.venv\Scripts\python.exe -m compileall bot`: 成功。

### 18bps DRY_RUN 再検証
- 一時設定: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `one_sided_quote_policy=current`, `dry_run=true`
- 実行: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 600`
- 起動確認: `env_DRY_RUN=1`, `dry_run=True`
- 検証後に `config.yaml` は `base_half_spread_bps=8.0`, `min_half_spread_bps=8.0`, `cancel_aggressive_policy=current`, `one_sided_quote_policy=current`, `dry_run=true` へ戻した。
- 直近10分: `cancel_aggressive_diagnostic=1463`, `cancel_aggressive=1463`, `quote_fade=903`, `order_new quote=0`
- `has_active_quote=true`: 0
- `used_px_source=active_quote`: 0
- `has_active_quote=false`: 3254（全診断ログ再集計）

### fix candidate simulation 再実行
- 実行: `.\.venv\Scripts\python.exe scripts\simulate_cancel_aggressive_fix_candidates.py`
- 出力: `reports\cancel_aggressive_fix_candidate_details.csv`, `reports\cancel_aggressive_fix_candidate_summary.csv`
- 再集計: `rows=3254`, `details=16270`
- `A_current`: `would_trigger_count=3254`, `would_trigger_ratio=1.0`
- `B_require_active_quote`: `would_trigger_count=0`, `no_active_quote_count=3254`
- `C_require_active_quote_and_fresh_trade`: `would_trigger_count=0`, `no_active_quote_count=3254`
- `D_require_active_quote_fresh_and_active_proximity`: `would_trigger_count=0`, `no_active_quote_count=3254`
- `E_require_active_quote_fresh_active_proximity_and_danger_match`: `would_trigger_count=0`, `no_active_quote_count=3254`
- `A_current trade_age_ms`: median 2793.5816ms, p90 13089.5912ms
- `A_current proximity_to_best_bps`: median 0.0, p90 0.2590

### 推論
- virtual active quote registry 自体はテストで登録/解除できている。
- 18bps DRY_RUN 実ログでは `cancel_aggressive` / `quote_fade` が quote 作成前に発火し続け、`order_new quote` まで到達していない。
- そのため今回の live DRY_RUN ログでは `has_active_quote=true` / `used_px_source=active_quote` の確認は未達。
- 次の焦点は、実判定変更前に `cancel_aggressive` の pre-quote 発火を診断上どう扱うか、または order_new / order_cancel ログから active quote を再構築できる相場サンプルを取ること。

### 未確定点
- active quote が存在する状態での `proximity_to_active_quote_bps` 分布は未確認。
- まだ `cancel_aggressive` 実判定変更、spread恒久変更、guard閾値変更、DRY_RUN=0 はしていない。
## 2026-04-25 pre_quote_decision 診断ログ・blocker 分析追加履歴

### 観測事実
- 対象: `bot/strategy/mm_funding.py`, `scripts/analyze_pre_quote_blockers.py`
- `event=risk`, `reason=pre_quote_decision` を追加した。
- 出力項目: `symbol`, `dry_run`, `base_half_spread_bps`, `min_half_spread_bps`, `expected_edge_bps`, `expected_spread_bps`, `funding_bps`, `cost_bps`, `adverse_buffer_bps`, `edge_pass`, `has_active_quote`, `active_quote_source`, `book_stale`, `funding_stale`, `inventory_block`, `max_inventory_block`, `unhedged_block`, `reject_streak_block`, `quote_fade_triggered`, `cancel_aggressive_triggered`, `tfi_fade_triggered`, `one_sided_suppressed_bid`, `one_sided_suppressed_ask`, `final_should_quote_bid`, `final_should_quote_ask`, `final_should_quote_any`, `final_block_reason`
- 追加: `scripts/analyze_pre_quote_blockers.py`
- 出力CSV:
  - `reports\pre_quote_blocker_details.csv`
  - `reports\pre_quote_blocker_summary.csv`

### テスト
- `.\.venv\Scripts\python.exe -m ruff check bot\strategy\mm_funding.py scripts\analyze_pre_quote_blockers.py`: All checks passed。
- `.\.venv\Scripts\python.exe -m pytest`: 22 passed。
- `.\.venv\Scripts\python.exe -m compileall bot`: 成功。

### 18bps DRY_RUN 再確認
- 一時設定: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `one_sided_quote_policy=current`, `dry_run=true`
- 実行: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 600`
- 起動確認: `env_DRY_RUN=1`, `dry_run=True`
- 検証後に `config.yaml` は `base_half_spread_bps=8.0`, `min_half_spread_bps=8.0`, `cancel_aggressive_policy=current`, `one_sided_quote_policy=current`, `dry_run=true` へ戻した。
- 分析実行: `.\.venv\Scripts\python.exe scripts\analyze_pre_quote_blockers.py`
- `pre_quote_decision` rows: 2323
- `base_half_spread_bps=18.0` rows: 2323
- `edge_pass=true`: 2323
- `final_should_quote_any=true`: 0
- `quote_fade_triggered_rows`: 860
- `cancel_aggressive_triggered_rows`: 1463
- `tfi_fade_triggered_rows`: 0

### final_block_reason 代表値
- `cancel_aggressive`: count 1463, ratio 0.6298, mean_expected_edge_bps 6.0171, median_expected_edge_bps 6.0200, edge_pass_ratio 1.0
- `quote_fade`: count 860, ratio 0.3702, mean_expected_edge_bps 6.0165, median_expected_edge_bps 6.0200, edge_pass_ratio 1.0

### 推論
- 18bps では EV 条件は全件 pass している。
- `order_new quote=0` の直接原因は edge 不足ではなく、quote 作成前の `cancel_aggressive` と `quote_fade`。
- `cancel_aggressive` が active quote 防御ではなく pre-quote blocker として強く効いている。
- `final_should_quote_any=true` が 0 のため、strategy から OMS までの発注経路ではなく、strategy 内 pre-quote guard で止まっている。

### 未確定点
- まだ `cancel_aggressive` 実判定変更、`quote_fade` 実判定変更、spread恒久変更、guard閾値変更、DRY_RUN=0 はしていない。

---

## 2026-04-25 cancel_aggressive_scope DRY_RUN 検証

### 観測事実
- `strategy.cancel_aggressive_scope` を追加。
- デフォルトは `pre_quote_and_active`。現行挙動維持。
- `config.yaml` に `strategy.cancel_aggressive_scope: pre_quote_and_active` を追加。
- 18bps 一時設定で 600秒 DRY_RUN 実施: `env_DRY_RUN=1`, `dry_run=True`。
- 一時設定: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `one_sided_quote_policy=current`, `dry_run=true`。
- 今回 run 抽出: `pre_quote_decision=3296`。
- `final_block_reason`: `none=1887`, `cancel_aggressive=664`, `quote_fade=745`。
- `reason=cancel_aggressive_scope_suppressed=942`。
- `order_new quote=1886`。
- `quote_lifetime median=0.24930834770202637 sec`。
- quote end: `cancel_aggressive=1328`, `quote_fade=558`。
- `has_active_quote=true` in `pre_quote_decision`: `945`。
- `used_px_source=active_quote`: `1328`。
- `simulate_cancel_aggressive_fix_candidates.py`: `E_require_active_quote_fresh_active_proximity_and_danger_match would_trigger_count=0`。
- 検証後に `config.yaml` を `8.0 / 8.0 / current / pre_quote_and_active / current / dry_run=true` に復元済み。

### 実装
- `bot/config.py`: `StrategyConfig.cancel_aggressive_scope` 追加。
- `bot/strategy/mm_funding.py`: `active_quote_only` かつ `has_active_quote=false` では `cancel_aggressive` を pre-quote blocker にしない分岐を追加。
- `bot/strategy/mm_funding.py`: `pre_quote_decision` に `cancel_aggressive_scope`, `cancel_aggressive_scope_suppressed`, `has_active_quote`, `final_block_reason` を出力。
- `tests/test_cancel_aggressive_scope.py` 追加。

### 検証
- `ruff check bot\strategy\mm_funding.py bot\config.py tests\test_cancel_aggressive_scope.py`: pass。
- `pytest tests\test_cancel_aggressive_scope.py`: `5 passed`。
- `pytest`: `27 passed`。
- `compileall bot`: pass。
- 分析再実行: `analyze_pre_quote_blockers.py`, `simulate_cancel_aggressive_fix_candidates.py`, `analyze_quote_lifecycle.py`, `analyze_cancel_aggressive_quality.py`。

### 推論
- `active_quote_only` で pre-quote blocker は減り、quote 作成は発生した。
- quote lifetime は短く、active quote 後の `cancel_aggressive` / `quote_fade` 再評価が次の確認点。
- E candidate は 0 件のため、active quote 近傍防御としての評価材料は不足。

### 未確定点
- `active_quote_only` は DRY_RUN 検証候補。本番採用なし。
- `DRY_RUN=0`、spread恒久変更、guard閾値変更、quote_fade変更、cancel_aggressive proximity閾値変更は未実施。

---

## 2026-04-25 active quote cancel_aggressive 品質分析

### 観測事実
- `scripts/analyze_active_quote_cancel_quality.py` を追加。
- 入力: `logs\*.jsonl` の `event=risk`, `reason=cancel_aggressive`。
- 対象: `has_active_quote=true` または `used_px_source=active_quote` の行。
- 出力CSV:
  - `reports\active_quote_cancel_quality_details.csv`
  - `reports\active_quote_cancel_quality_summary.csv`
- 実行: `.\.venv\Scripts\python.exe scripts\analyze_active_quote_cancel_quality.py`
- 結果: `rows=664`, `valid_candidate_count=0`, `valid_candidate_ratio=0.0`。
- `trade_age_ms`: mean `3141.0946`, median `1827.8118`, p90 `8620.3772`。
- `proximity_to_active_quote_bps`: mean `30.4057`, median `35.7421`, p90 `37.5343`。
- `proximity_to_best_bps`: mean `0.0379`, median `0.0`。
- `danger_match_count=664`, `danger_match_ratio=1.0`。
- `quote_fade_nearby_count=562`, `quote_fade_nearby_ratio=0.8464`。
- trade_side別:
  - `buy`: count `408`, valid `0`, proximity median `35.7463`, quote_fade_nearby_ratio `0.8407`。
  - `sell`: count `256`, valid `0`, proximity median `20.2493`, quote_fade_nearby_ratio `0.8555`。

### 推論
- active quote 後の `cancel_aggressive` は方向一致だけは満たす。
- `trade_age_ms` が大きく、古い `last_public_trade` の再利用疑いが強い。
- `proximity_to_active_quote_bps` が大きく、active quote 近傍ではなく best 近傍 trade で cancel している可能性が高い。
- `quote_fade_nearby_ratio=0.8464` のため、`cancel_aggressive` 単独ではなく `quote_fade` と同じ危険局面を重複検知している可能性が高い。

### 未確定点
- 今回は分析のみ。`cancel_aggressive` 実判定、fresh 閾値、proximity 閾値、`quote_fade` 判定は未変更。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更は未実施。

---

## 2026-04-25 cancel_aggressive_quality_filter DRY_RUN 検証

### 観測事実
- `strategy.cancel_aggressive_quality_filter` を追加。
- 設定値: `off`, `fresh_active_quote_proximity`。
- デフォルトは `off`。現行挙動維持。
- `strategy.cancel_aggressive_max_trade_age_ms: 500.0` を追加。
- `strategy.cancel_aggressive_active_proximity_bps: 1.0` を追加。
- `config.yaml` に `cancel_aggressive_quality_filter: off` を追加。
- `tests/test_cancel_aggressive_quality_filter.py` を追加。

### DRY_RUN 検証
- 一時設定: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `cancel_aggressive_quality_filter=fresh_active_quote_proximity`, `one_sided_quote_policy=current`, `dry_run=true`。
- 実行: `.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --python-exe .\.venv\Scripts\python.exe --config config.yaml --duration-sec 600`
- 起動確認: `env_DRY_RUN=1`, `dry_run=True`。
- 今回 run 抽出: `pre_quote_decision=3755`。
- `final_block_reason`: `none=2908`, `quote_fade=847`。
- `reason=cancel_aggressive_quality_suppressed=954`。
- `reason=cancel_aggressive=0`。
- `order_new quote=1053`。
- `quote_lifetime median=0.2580299377441406 sec`。
- quote end: `tfi_fade=574`, `quote_fade=451`, `order_cancel:quote=28`, `cancel_aggressive=0`。
- active quality rows: `954`。
- `valid_candidate_count=0`, `valid_candidate_ratio=0.0`。
- `trade_age_ms median=2557.6974`, `p90=10161.9854`。
- `proximity_to_active_quote_bps median=36.7927`, `p90=38.1991`。
- quality suppress の `quote_fade_nearby_ratio=0.5283`。
- 分析再実行: `analyze_pre_quote_blockers.py`, `analyze_quote_lifecycle.py`, `analyze_active_quote_cancel_quality.py`。

### 比較
- 前回 active_quote_only / 18bps: `order_new quote=1886`, `quote_lifetime median=0.2493 sec`, quote end `cancel_aggressive=1328`, `quote_fade=558`, `valid_candidate_ratio=0.0`。
- 今回 quality filter: `order_new quote=1053`, `quote_lifetime median=0.2580 sec`, quote end `cancel_aggressive=0`, `quote_fade=451`, `tfi_fade=574`, `valid_candidate_ratio=0.0`。

### 推論
- quality filter により `cancel_aggressive` end は 0 まで減った。
- quote lifetime median は `0.2493 -> 0.2580 sec` で改善幅は小さい。
- `valid_candidate_ratio=0.0` のままで、active quote 近傍の aggressive trade は捕捉できていない。
- `cancel_aggressive` 抑制後は `tfi_fade` / `quote_fade` が主な終了理由になった。

### 検証
- `ruff check bot\config.py bot\strategy\mm_funding.py tests\test_cancel_aggressive_quality_filter.py`: pass。
- `pytest tests\test_cancel_aggressive_quality_filter.py`: `8 passed`。
- `pytest`: `35 passed`。
- `compileall bot`: pass。
- 検証後に `config.yaml` を `8.0 / 8.0 / current / pre_quote_and_active / off / current / dry_run=true` に復元済み。

### 未確定点
- `fresh_active_quote_proximity` は DRY_RUN 検証候補。本番採用なし。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、`quote_fade` 判定変更は未実施。

---

## 2026-04-25 quote_fade / tfi_fade exit quality 分析

### 観測事実
- `scripts/analyze_quote_fade_tfi_fade_exit_quality.py` を追加。
- 入力: `logs\*.jsonl` と lifecycle。root の `reports\quote_lifecycle_details.csv` が無い場合はログから再構築し、既存 `reports\spread_dryrun_compare\quote_lifecycle_details.csv` も補助的に読む。
- 出力CSV:
  - `reports\quote_fade_tfi_fade_exit_quality_details.csv`
  - `reports\quote_fade_tfi_fade_exit_quality_summary.csv`
- 実行: `.\.venv\Scripts\python.exe scripts\analyze_quote_fade_tfi_fade_exit_quality.py`
- 出力: `rows=1611`, `quote_fade=1014`, `tfi_fade=597`。
- forward return 非空率: `ret_1s_bps=1607/1611`, `ret_3s_bps=1605/1611`, `ret_5s_bps=1603/1611`。

### 代表値
- `quote_fade 1s`: success `0.0394`, fail `0.0237`, neutral `0.9369`, mean directional ret `0.0140bps`。
- `quote_fade 3s`: success `0.1105`, fail `0.0720`, neutral `0.8176`, mean directional ret `0.0490bps`。
- `quote_fade 5s`: success `0.1746`, fail `0.1183`, neutral `0.7071`, mean directional ret `0.0659bps`。
- `tfi_fade 1s`: success `0.0184`, fail `0.0352`, neutral `0.9464`, mean directional ret `-0.0172bps`。
- `tfi_fade 3s`: success `0.0838`, fail `0.0955`, neutral `0.8208`, mean directional ret `-0.0294bps`。
- `tfi_fade 5s`: success `0.1256`, fail `0.1407`, neutral `0.7337`, mean directional ret `-0.0329bps`。
- `quote_fade` leg別 3s:
  - ask: success `0.1100`, fail `0.0727`, mean directional ret `0.0487bps`。
  - bid: success `0.1109`, fail `0.0713`, mean directional ret `0.0493bps`。
- `tfi_fade` leg別 3s:
  - ask: success `0.0894`, fail `0.0960`, mean directional ret `-0.0229bps`。
  - bid: success `0.0780`, fail `0.0949`, mean directional ret `-0.0360bps`。

### 推論
- `quote_fade` は success が fail を上回り、mean directional ret も 1/3/5s でプラス。ただし neutral が高く、有効性は弱いプラス寄り。
- `tfi_fade` は 1/3/5s すべてで fail が success を上回り、mean directional ret がマイナス。過敏疑い。
- `tfi_fade` は quote_lifetime を短くしているが、今回ログでは TFI 方向への短期価格追随は確認できない。
- bid/ask 別でも `tfi_fade` は両側で弱い。

### 検証
- `ruff check scripts\analyze_quote_fade_tfi_fade_exit_quality.py`: pass。
- `compileall scripts\analyze_quote_fade_tfi_fade_exit_quality.py`: pass。

### 未確定点
- 今回は分析のみ。`quote_fade` / `tfi_fade` / `cancel_aggressive` の実判定は未変更。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更は未実施。

---

## 2026-04-25 tfi_fade_policy DRY_RUN 比較

### 観測事実
- `strategy.tfi_fade_policy` を追加。
- 設定値: `current`, `disabled`, `threshold_0p7`, `threshold_0p8`。
- デフォルトは `current`。現行挙動維持。
- `config.yaml` に `tfi_fade_policy: current` を追加。
- `tests/test_tfi_fade_policy.py` を追加。
- 比較出力:
  - `reports\tfi_fade_policy_compare\current\`
  - `reports\tfi_fade_policy_compare\disabled\`
  - `reports\tfi_fade_policy_compare\threshold_0p7\`
  - `reports\tfi_fade_policy_compare\threshold_0p8\`
  - `reports\tfi_fade_policy_compare\tfi_fade_policy_compare_summary.md`

### DRY_RUN 条件
- 共通一時設定: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `cancel_aggressive_quality_filter=fresh_active_quote_proximity`, `one_sided_quote_policy=current`, `dry_run=true`。
- 各 policy で 600秒 DRY_RUN 実施。
- 全 run で起動ログ `env_DRY_RUN=1`, `dry_run=True` を確認。
- 各 run 後に `analyze_pre_quote_blockers.py`, `analyze_quote_lifecycle.py`, `analyze_quote_fade_tfi_fade_exit_quality.py` を再実行。

### 比較結果
- `current`: `order_new quote=1080`, `quote_lifetime median=0.256299`, `mean=0.286497`, end `tfi_fade=558`, `quote_fade=492`, `cancel_aggressive=0`, `order_cancel:quote=30`, `tfi_fade_suppressed=0`。
- `disabled`: `order_new quote=1040`, `quote_lifetime median=0.498965`, `mean=0.702222`, end `tfi_fade=0`, `quote_fade=942`, `cancel_aggressive=0`, `order_cancel:quote=98`, `tfi_fade_suppressed=1237`。
- `threshold_0p7`: `order_new quote=1046`, `quote_lifetime median=0.248277`, `mean=0.311442`, end `tfi_fade=548`, `quote_fade=457`, `cancel_aggressive=0`, `order_cancel:quote=41`, `tfi_fade_suppressed=86`。
- `threshold_0p8`: `order_new quote=1055`, `quote_lifetime median=0.254590`, `mean=0.342078`, end `tfi_fade=542`, `quote_fade=460`, `cancel_aggressive=0`, `order_cancel:quote=53`, `tfi_fade_suppressed=167`。
- `disabled` の median lifetime は current 比 `1.947x`。
- `threshold_0p7` の median lifetime は current 比 `0.969x`。
- `threshold_0p8` の median lifetime は current 比 `0.993x`。

### 推論
- `disabled` では quote lifetime が大きく伸び、tfi_fade 過敏疑いは強まった。
- ただし `disabled` は `quote_fade` end が `492 -> 942` に増え、主終了理由が quote_fade へ移った。
- `threshold_0p7` / `threshold_0p8` は tfi_fade_suppressed が少なく、quote_lifetime median は current とほぼ同等。単独改善は弱い。
- 次は `quote_fade` 側の精査が優先候補。

### 検証
- `ruff check bot\config.py bot\strategy\mm_funding.py tests\test_tfi_fade_policy.py`: pass。
- `pytest tests\test_tfi_fade_policy.py`: `6 passed`。
- `pytest`: `41 passed`。
- `compileall bot`: pass。
- 検証後に `config.yaml` を `8.0 / 8.0 / current / pre_quote_and_active / off / current / current / dry_run=true` に復元済み。

### 未確定点
- `tfi_fade_policy` は DRY_RUN 検証候補。本番採用なし。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、`quote_fade` 判定変更、`cancel_aggressive` 判定変更は未実施。

---

## 2026-04-26 quote_fade_policy DRY_RUN 比較

### 観測事実
- `strategy.quote_fade_policy` を追加。
- 設定値: `current`, `disabled`, `threshold_5bps`, `threshold_8bps`, `threshold_10bps`。
- デフォルトは `current`。現行挙動維持。
- `config.yaml` に `quote_fade_policy: current` を追加。
- `tests/test_quote_fade_policy.py` を追加。
- 比較出力:
  - `reports\quote_fade_policy_compare\current\`
  - `reports\quote_fade_policy_compare\disabled\`
  - `reports\quote_fade_policy_compare\threshold_5bps\`
  - `reports\quote_fade_policy_compare\threshold_8bps\`
  - `reports\quote_fade_policy_compare\threshold_10bps\`
  - `reports\quote_fade_policy_compare\quote_fade_policy_compare_summary.md`

### DRY_RUN 条件
- 共通一時設定: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `cancel_aggressive_quality_filter=fresh_active_quote_proximity`, `tfi_fade_policy=disabled`, `one_sided_quote_policy=current`, `dry_run=true`。
- 各 policy で 600秒 DRY_RUN 実施。
- 全 run で起動ログ `env_DRY_RUN=1`, `dry_run=True` を確認。
- 各 run 後に `analyze_pre_quote_blockers.py`, `analyze_quote_lifecycle.py`, `analyze_quote_fade_tfi_fade_exit_quality.py` を再実行。

### 比較結果
- `current`: `order_new quote=990`, `quote_lifetime median=0.516929`, `mean=0.776587`, end `quote_fade=753`, `tfi_fade=0`, `cancel_aggressive=0`, `order_cancel:quote=235`, `quote_fade_suppressed=0`。
- `disabled`: `order_new quote=172`, `quote_lifetime median=0.489123`, `mean=3.562227`, end `quote_fade=0`, `tfi_fade=0`, `cancel_aggressive=0`, `order_cancel:quote=171`, `quote_fade_suppressed=907`。
- `threshold_5bps`: `order_new quote=961`, `quote_lifetime median=0.512540`, `mean=0.838652`, end `quote_fade=829`, `tfi_fade=0`, `cancel_aggressive=0`, `order_cancel:quote=130`, `quote_fade_suppressed=261`。
- `threshold_8bps`: `order_new quote=640`, `quote_lifetime median=0.784076`, `mean=1.426112`, end `quote_fade=544`, `tfi_fade=0`, `cancel_aggressive=0`, `order_cancel:quote=94`, `quote_fade_suppressed=538`。
- `threshold_10bps`: `order_new quote=584`, `quote_lifetime median=0.998460`, `mean=1.609195`, end `quote_fade=459`, `tfi_fade=0`, `cancel_aggressive=0`, `order_cancel:quote=124`, `quote_fade_suppressed=623`。
- median lifetime 比 current:
  - `disabled=0.946x`
  - `threshold_5bps=0.992x`
  - `threshold_8bps=1.517x`
  - `threshold_10bps=1.932x`

### 推論
- `disabled` は quote_fade end を消すが、quote 件数が `990 -> 172` に落ち、`order_cancel:quote` に寄った。単純無効化は候補にしにくい。
- `threshold_5bps` は current と寿命がほぼ同じで、単独改善は弱い。
- `threshold_8bps` / `threshold_10bps` は寿命を伸ばしたが、quote 件数が減り、10bps は current の2倍に届かない。
- quote_fade policy 単独では根本改善としては弱く、threshold を上げるほど quote 機会も落ちる。

### 検証
- `ruff check bot\config.py bot\strategy\mm_funding.py tests\test_quote_fade_policy.py`: pass。
- `pytest tests\test_quote_fade_policy.py`: `7 passed`。
- `pytest`: `48 passed`。
- `compileall bot`: pass。
- 検証後に `config.yaml` を `8.0 / 8.0 / current / pre_quote_and_active / off / current / current / current / dry_run=true` に復元済み。

### 未確定点
- `quote_fade_policy` は DRY_RUN 検証候補。本番採用なし。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、`cancel_aggressive` 判定変更、`tfi_fade` 判定変更は未実施。

---

## 2026-04-26 quote_fade_policy quality 分析

### 観測事実
- `scripts\analyze_quote_fade_policy_quality.py` を追加。
- 対象: `reports\quote_fade_policy_compare\current\`, `threshold_8bps\`, `threshold_10bps\`。
- 出力:
  - `reports\quote_fade_policy_compare\quote_fade_policy_quality_details.csv`
  - `reports\quote_fade_policy_compare\quote_fade_policy_quality_summary.csv`
- details rows: `2214`、summary rows: `20`。

### 代表値
- `current`: `quote_count=990`, `median_lifetime=0.516929`, `mean_lifetime=0.776587`, `quote_fade_end=753`, `order_cancel:quote=235`, `quote_fade_suppressed=0`, `mean_directional_ret_3s=0.101879`。
- `threshold_8bps`: `quote_count=640`, `median_lifetime=0.784076`, `mean_lifetime=1.426112`, `quote_fade_end=544`, `order_cancel:quote=94`, `quote_fade_suppressed=538`, `danger_after_suppression_ratio_3s=0.160356`, `5s=0.187082`, `mean_directional_ret_3s=0.070311`。
- `threshold_10bps`: `quote_count=584`, `median_lifetime=0.998460`, `mean_lifetime=1.609195`, `quote_fade_end=459`, `order_cancel:quote=124`, `quote_fade_suppressed=623`, `danger_after_suppression_ratio_3s=0.088235`, `5s=0.167421`, `mean_directional_ret_3s=0.049975`。

### 推論
- `threshold_10bps` は寿命最長だが、quote 件数が `990 -> 584` に減り、`order_cancel:quote` が `threshold_8bps` より多い。
- `threshold_8bps` は quote 件数 `640`、median lifetime `0.784076`、`order_cancel:quote=94` で、10bps より機会と寿命のバランスが良い次候補。
- `current` は quote 件数は多いが `quote_fade_end=753` で過敏寄り。

### 検証
- `ruff check scripts\analyze_quote_fade_policy_quality.py`: pass。
- `compileall scripts\analyze_quote_fade_policy_quality.py`: pass。
- `python scripts\analyze_quote_fade_policy_quality.py`: pass。
- `config.yaml` は変更なし。現状 `8.0 / 8.0 / current / pre_quote_and_active / off / current / current / current / dry_run=true`。

### 未確定点
- `threshold_8bps` は次の DRY_RUN 候補。本番採用なし。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、実判定変更は未実施。

---

## 2026-04-26 threshold_8bps extended DRY_RUN

### 観測事実
- 統合条件で 30分 DRY_RUN を実施。
- 条件: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `cancel_aggressive_quality_filter=fresh_active_quote_proximity`, `tfi_fade_policy=disabled`, `quote_fade_policy=threshold_8bps`, `one_sided_quote_policy=current`, `dry_run=true`。
- 起動ログで `env_DRY_RUN=1`, `dry_run=True` を確認。
- 保存先: `reports\threshold_8bps_extended_dryrun\`
- 対象期間ログ抜粋: `reports\threshold_8bps_extended_dryrun\logs\`
- summary: `reports\threshold_8bps_extended_dryrun\threshold_8bps_extended_summary.md`
- metrics: `reports\threshold_8bps_extended_dryrun\threshold_8bps_extended_metrics.json`

### 代表値
- `pre_quote_decision_rows=13595`
- `order_new quote=1391`
- `quote_lifetime median=1.262800 sec`
- `quote_lifetime mean=1.999645 sec`
- `quote_lifetime p75=2.736997 sec`
- `quote_lifetime p90=5.036344 sec`
- end: `quote_fade=865`, `tfi_fade=0`, `cancel_aggressive=0`, `order_cancel:quote=526`
- suppressed: `quote_fade_suppressed=1518`, `tfi_fade_suppressed=5142`, `cancel_aggressive_quality_suppressed=6017`
- `final_block_reason`: `none=13028`, `quote_fade=567`
- quote_fade exit quality 3s: `success_ratio=0.131792`, `fail_ratio=0.079769`, `neutral_ratio=0.788439`, `mean_directional_ret_bps=0.180568`
- quote_fade exit quality 5s: `success_ratio=0.203468`, `fail_ratio=0.131792`, `neutral_ratio=0.664740`, `mean_directional_ret_bps=0.188410`
- active cancel: `active_cancel_rows=0`, `valid_candidate_ratio=null`

### 前回比較
- `current`: `quote=990`, `median_lifetime=0.516929 sec`, `quote_fade_end=753`, `order_cancel:quote=235`
- `threshold_8bps`: `quote=640`, `median_lifetime=0.784076 sec`, `quote_fade_end=544`, `order_cancel:quote=94`, `danger_after_suppression_3s=0.160356`

### 推論
- `median_lifetime=1.262800 sec` で基準の `0.75 sec` を維持。`threshold_8bps` は継続 DRY_RUN 候補。
- quote 件数は 30分で `1391`。短時間比較より極端な機会減には見えない。
- `order_cancel:quote=526` は多く、quote を残した結果の自然キャンセル増は要確認。
- quote_fade exit quality は 3s / 5s とも success_ratio が fail_ratio を上回り、mean directional return はプラス。ただし neutral_ratio が高く、強い危険回避とは断定しない。
- active cancel は `active_cancel_rows=0` のため、cancel_aggressive 本番採用材料は不足。

### 検証
- `run_bot_for_duration.py --duration-sec 1800`: pass。
- `analyze_pre_quote_blockers.py`: pass。
- `analyze_quote_lifecycle.py`: pass。
- `analyze_quote_fade_tfi_fade_exit_quality.py`: pass。
- `analyze_quote_fade_policy_quality.py`: pass。
- `analyze_active_quote_cancel_quality.py`: pass。
- 検証後に `config.yaml` を `8.0 / 8.0 / current / pre_quote_and_active / off / current / current / current / dry_run=true` に復元済み。

### 未確定点
- 30分1回のみ。別時間帯30分は未実施。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、実判定変更は未実施。

---

## 2026-04-26 threshold_8bps extended DRY_RUN 2回目

### 観測事実
- 統合条件で別時間帯 30分 DRY_RUN を実施。
- 条件: `base_half_spread_bps=18.0`, `min_half_spread_bps=18.0`, `cancel_aggressive_policy=current`, `cancel_aggressive_scope=active_quote_only`, `cancel_aggressive_quality_filter=fresh_active_quote_proximity`, `tfi_fade_policy=disabled`, `quote_fade_policy=threshold_8bps`, `one_sided_quote_policy=current`, `dry_run=true`。
- 起動ログで `env_DRY_RUN=1`, `dry_run=True` を確認。
- 保存先: `reports\threshold_8bps_extended_dryrun_2\`
- 対象期間ログ抜粋: `reports\threshold_8bps_extended_dryrun_2\logs\`
- summary: `reports\threshold_8bps_extended_dryrun_2\threshold_8bps_extended_summary.md`
- metrics: `reports\threshold_8bps_extended_dryrun_2\threshold_8bps_extended_metrics.json`

### 代表値
- `pre_quote_decision_rows=13437`
- `order_new quote=1648`
- `quote_lifetime median=1.007175 sec`
- `quote_lifetime mean=1.525694 sec`
- `quote_lifetime p75=2.254137 sec`
- `quote_lifetime p90=3.516080 sec`
- end: `quote_fade=800`, `tfi_fade=0`, `cancel_aggressive=0`, `order_cancel:quote=846`
- suppressed: `quote_fade_suppressed=1245`, `tfi_fade_suppressed=4711`, `cancel_aggressive_quality_suppressed=5929`
- `final_block_reason`: `none=12877`, `quote_fade=560`
- quote_fade exit quality 3s: `success_ratio=0.256250`, `fail_ratio=0.137500`, `neutral_ratio=0.606250`, `mean_directional_ret_bps=0.245467`
- quote_fade exit quality 5s: `success_ratio=0.347500`, `fail_ratio=0.208750`, `neutral_ratio=0.443750`, `mean_directional_ret_bps=0.252714`
- active cancel: `active_cancel_rows=0`, `valid_candidate_ratio=null`

### 1回目比較
- 1回目: `quote=1391`, `median=1.262800 sec`, `mean=1.999645 sec`, `p75=2.736997 sec`, `p90=5.036344 sec`
- 2回目: `quote=1648`, `median=1.007175 sec`, `mean=1.525694 sec`, `p75=2.254137 sec`, `p90=3.516080 sec`
- 1回目 end/suppressed: `quote_fade=865`, `order_cancel:quote=526`, `quote_fade_suppressed=1518`, `tfi_fade_suppressed=5142`, `cancel_aggressive_quality_suppressed=6017`
- 2回目 end/suppressed: `quote_fade=800`, `order_cancel:quote=846`, `quote_fade_suppressed=1245`, `tfi_fade_suppressed=4711`, `cancel_aggressive_quality_suppressed=5929`
- 1回目 final_block_reason: `none=13028`, `quote_fade=567`
- 2回目 final_block_reason: `none=12877`, `quote_fade=560`

### 推論
- 2回目も `median_lifetime=1.007175 sec` で基準の `0.75 sec` を再現。`threshold_8bps` は継続 DRY_RUN 候補。
- quote 件数は `1648` で、機会損失は限定的。
- `order_cancel:quote=846` は1回目 `526` より増加。quote を残した副作用として要注意。
- quote_fade exit quality は 3s / 5s とも success_ratio が fail_ratio を上回り、mean directional return はプラス。
- active cancel は `active_cancel_rows=0` のため、cancel_aggressive 本番採用材料は不足。

### 検証
- `run_bot_for_duration.py --duration-sec 1800`: pass。
- `analyze_pre_quote_blockers.py`: pass。
- `analyze_quote_lifecycle.py`: pass。
- `analyze_quote_fade_tfi_fade_exit_quality.py`: pass。
- `analyze_quote_fade_policy_quality.py`: pass。
- `analyze_active_quote_cancel_quality.py`: pass。
- 検証後に `config.yaml` を `8.0 / 8.0 / current / pre_quote_and_active / off / current / current / current / dry_run=true` に復元済み。

### 未確定点
- 2回とも寿命改善は再現。ただし `order_cancel:quote` 増加の意味は未確定。
- `DRY_RUN=0`、本番採用、spread恒久変更、guard閾値変更、実判定変更は未実施。

---

## 2026-04-27 cost 実測反映 / 統合 policy 本採用 / sim_fill 改修 / 移管準備

### 観測事実
- `scripts/check_vip_tier.py` 新規。`/api/v2/common/trade-rate` 実測:
  - `spot makerFeeRate=10.0bps takerFeeRate=10.0bps` (VIP0)
  - `mix(USDT-FUTURES) makerFeeRate=1.4bps takerFeeRate=4.2bps` (VIP3 相当)
- 旧 `cost.fee_maker_perp_bps=2.0` は実態より 0.6bps 高かった。
- 旧 sim_fill は perp BBO 価格で fill + fee=0 の簡易版で、post_only spread を取れない設計。

### 実装
- `config.yaml`:
  - `cost.fee_maker_perp_bps: 1.4` (実測反映)
  - `strategy.base_half_spread_bps: 18.0` / `min_half_spread_bps: 18.0`
  - `quote_fade_policy: threshold_8bps`
  - `cancel_aggressive_scope: active_quote_only`
  - `cancel_aggressive_quality_filter: fresh_active_quote_proximity`
  - `tfi_fade_policy: disabled`
  - `min_funding_rate: 0.0` (positive funding なら常時 quote)
- `bot/app.py`: `_simulate_fills_loop` を改修
  - active quote 価格 (`oms.active_quote_snapshot()`) を fill 価格に使用
  - perp maker fee / spot taker fee を qty*px*rate で計上
  - active quote 無い時は fill skip
- `scripts/plot_pnl.py` 新規 (matplotlib + plotly 両版)
- `MIGRATION.md` 新規 (別 PC 移管 + 常駐稼働手順)
- `scripts/start_bot.ps1` / `scripts/stop_bot.ps1` 新規 (auto-restart, graceful stop)
- `.gitignore` に `reports/` 追加

### DRY_RUN 検証 (sim_fill 改修後)
| 設定 | net_pnl/30min | net/h 外挿 | 備考 |
|---|---|---|---|
| min_funding=0.00002, qty=0.01 | +18.01 USDT | 37.3 | Step 1 |
| qty=0.01, interval=15s | +2.48/10min | - | 線形性確認 |
| qty=0.01, interval=30/60s | 0 | - | funding_off 時間帯 |
| min_funding=0.0, qty=0.01 | +24.61 USDT | 50.9 | funding_off=0 で 100% 稼働 |
| min_funding=0.0, qty=0.02 | +50.49 USDT | 104.5 | lot 2x で PnL も 2x (線形) |

### 推論
- 旧「構造的赤字 EV/往復 -6〜-10bps」は cost 過大評価 + sim_fill 設計欠陥に起因。
- 新 cost (24.8bps) + 18bps spread + 統合 policy で理論 EV +9.78bps、PnL ベースでも明確に黒字方向。
- 実 fill 率は DRY_RUN では測定不能。係数 0.05〜0.1 想定で qty=0.01 年 22k〜45k USDT、qty=0.02 で 46k〜91k USDT 見積。
- `min_funding_rate=0.0` で稼働率 97%、`enable_only_positive_funding=true` 維持で funding negative 時は自動停止 (safety 維持)。

### 移管準備
- `MIGRATION.md` に環境構築・DRY_RUN=0 移行・常駐稼働・監視・緊急停止手順を網羅。
- 移管後 (別 PC) の DRY_RUN=0 微小ロット (target=100, max=200) → 段階拡大 を推奨。
- `D` (quote_fill_rate / adverse_fill_rate ログ精査): 設計あり、`adverse_fill_horizon_sec` 未使用が改善余地。本番 (DRY_RUN=0) 後優先。

### 未確定点
- DRY_RUN=0 PnL 実測未実施 (資金確保待ち)。
- 実 fill 率係数。adverse selection の実環境影響。
- `quote_replace_count` が min_funding=0 設定で 11→34/分に増加した理由 (要分析候補)。
---

## 2026-05-02 45115 price tick multiple 前提更新 / PERP price rounding 修正

### 観測事実
- `orders.jsonl` 末尾の `resp_code=45115` は、cancel race ではなく Bitget 公式エラーコード上の price tick / multiple 不整合 reject として扱う。
- `config.yaml` は `dry_run: false`、`.env` は `DRY_RUN="0"` のため、DRY_RUN=0 の再起動は禁止。
- `decision.jsonl` / `orders.jsonl` は 2026-04-30 06:52:05 JST で停止し、`system.jsonl` / `pnl.jsonl` は更新継続。
- `validate_logs_strict.ps1 -LogDir logs` は既存ログに対して `halt_strict_violation=7657` で FAIL。

### 実装
- `bot/exchange/constraints.py`
  - `get_price_tick` 追加。PERP constraints の `pricePlace` がある場合は `10 ** -pricePlace` を tick とする。
  - `quantize_perp_price` 追加。Decimal で buy は下方向、sell は上方向へ tick multiple 丸め。
  - `format_price_for_bitget` 追加。Decimal を REST payload 用文字列へ変換。
- `bot/exchange/bitget_gateway.py`
  - `_parse_perp_constraints` / `_parse_spot_constraints` で `price_place` を保持。
  - PERP `place_order` 直前で Decimal による最終丸めを実施し、price を文字列 payload に変換。
- `bot/oms/oms.py`
  - quote upsert と submit 前の PERP price 丸めを side-aware Decimal rounding に変更。
  - `order_new` と `order_reject` ログへ `price_before_round` / `price_after_round` / `price_payload` / `tick_size` / `pricePlace` を追加。
- `config.yaml`
  - `reject_streak_limit: 10` 変更は中止し、`5` に戻した。
- `tests/test_perp_price_rounding.py`
  - tick `0.01` / `0.1` / `0.001` で buy 下丸め、sell 上丸めを確認する単体テストを追加。

### 推論
- 45115 再発防止の主対象は reject_streak 緩和ではなく、PERP order price の tick multiple 丸めと constraints 適用経路。
- `45115` は reject_streak から除外しない。
- 実発注再起動は未実施。

### 検証
- `.venv\Scripts\python.exe -m ruff check bot tests`: pass。
- `.venv\Scripts\python.exe -m pytest`: 51 passed。

### 未確定点
- DRY_RUN=0 での再起動・実発注検証は未実施。
- 既存稼働プロセスは停止・再起動していない。

---

## 2026-05-02 再稼働後 price tick rounding 確認

### 観測事実
- 新 PID: `20268`
- 実行: `.venv\Scripts\python.exe -m bot.app --config config.yaml`
- 起動時刻相当: `2026-05-02 01:43:17 JST`
- `DRY_RUN=0`
- private WS 有効。
- `constraints_loaded`: `spot_ready=true`, `perp_ready=true`
- public/private WS 接続済み。
- 再稼働後 `order_new`: `0`
- 再稼働後 `45115`: `0`
- 再稼働後 `order_reject`: `0`
- 現在は `enable_only_positive_funding=true` かつ `funding_rate=-5.1e-05` のため `funding_off`。
- `final_should_quote_bid/ask` が true の局面でも、最終 action は `funding_off`。
- active quote はない。

### 推論
- 45115 再発は現時点で確認されていない。
- ただし再稼働後に `order_new` が出ていないため、price tick rounding 修正の live 検証は未完了。

### 次回確認
- 次回 positive funding で `order_new` が出たときに、`price_before_round` / `price_after_round` / `price_payload` / `tick_size` / `pricePlace` のログを確認する。
- funding gate を DRY_RUN=0 で無理に外して検証しない。
- 実発注確認は自然に quote 条件が満たされたタイミングで行う。

### 実装変更
- なし。

---

## 2026-05-04 稼働レビュー指摘 1-4 修正

### 観測事実
- 稼働中 PID `20268` は `.venv\Scripts\python.exe -m bot.app --config config.yaml` の実プロセス。
- 直近ログでは `order_new/order_cancel` は継続し、`resp_code=00000` のみ。直近 30 分の fill は 0。
- `decision.jsonl` は約 5GB まで肥大化しており、既存 `JsonlLogger` は append のみでローテーション無しだった。
- `oms.sync_positions()` は task として起動されていたが、実装は一回同期して終了する形だった。

### 実装
- `config.yaml`
  - 約定ゼロ対策として `strategy.base_half_spread_bps: 14.0`、`strategy.min_half_spread_bps: 14.0` に変更。
  - 18bps からの段階的な引き下げ。稼働中プロセスには再起動まで反映されない。
- `bot/log/jsonl.py`
  - JSONL ロガーへサイズローテーション、日次ローテーション、rotated file の gzip 圧縮を追加。
  - 既定: `LOG_ROTATE_MAX_BYTES=536870912`、`LOG_ROTATE_DAILY=1`、`LOG_ROTATE_GZIP=1`。
  - gzip は書き込みブロックを避けるため background thread で実行。
- `bot/app.py`
  - `runtime_heartbeat` を 60 秒ごとに `system.jsonl` へ記録。PID / PPID / Python executable を監視可能にした。
- `bot/oms/oms.py`
  - `sync_positions()` を定期ループ化し、起動時 1 回だけで終わらないよう修正。
  - positions 変更時または 60 秒ごとに `positions_sync` を記録し、`delta` / `unhedged_qty` / `unhedged_since` を追加。
- `scripts/start_bot.ps1`
  - PID ファイル検証を `Get-CimInstance Win32_Process` の command line 照合へ強化。
  - stdout/stderr を run id 付きファイルへ分離し、`logs/bot.run.json` に PID / run_id / log path / command を保存。
- `scripts/stop_bot.ps1`
  - 実挙動に合わせて停止メッセージとコメントを修正。
- `tests/test_jsonl_rotation.py`
  - size / daily rotation の単体テストを追加。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\log\jsonl.py bot\oms\oms.py bot\app.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q`: 53 passed。

### 推論
- 14bps は 18bps より fill 率改善を狙う一方、8bps へ即戻しするより EV 悪化リスクを抑える中間値。
- `sync_positions()` の一回終了は、未ヘッジ/target inventory 監視の信頼性を下げる構造問題だった。
- ログローテーションは次回プロセス起動後から有効。既存 5GB ファイルは初回ローテーション対象になる。

### 未確定点
- 稼働中プロセスは停止・再起動していないため、14bps 設定、runtime heartbeat、定期 position sync、ログローテーションの live 反映は未実施。
- 14bps で実 fill 率が改善するかは未検証。
- 既存 5GB `decision.jsonl` の gzip 圧縮にかかる時間・I/O 負荷は未測定。

---

## 2026-05-06 live STOPPED/funding_off 片脚ポジション調査

### 観測事実
- 稼働中プロセスは `pid=20752` / `ppid=6440` で、`python -m bot.app --config config.yaml` が継続稼働中。
- `dry_run=false`、直近 `decision.jsonl` は `mode=STOPPED` / `reason=funding_off`、`funding_rate=-2.5e-05` 付近。
- `orders.jsonl` の `positions_sync` は `spot_pos=0.0` / `perp_pos=0.02` / `delta=0.02` を 2026-05-04 17:05:05 以降継続記録。
- read-only private WS 追加確認で Bitget raw positions に `ETHUSDT long total=0.02 available=0.02 openPriceAvg=2353.255 posMode=one_way_mode` を確認。
- 最後の quote order は 2026-05-06 10:36:41 `QUOTE_ASK sell 0.02 @ 2375.6`、2026-05-06 10:36:43 に `funding_below_min` で cancel。
- `max_unhedged_notional` は `pnl.jsonl` 集計上 0 のまま。`unhedged_exceeded` / `max_position` / `flatten` 発火ログは確認できない。
- `fills.jsonl` は futures fill 4 件すべて `size=0.0`。旧 MVP parser は futures fill quantity に `baseVolume` を読んでいたが、現行 `OMS._parse_fill()` は `baseVolume` を候補に含めていない。

### 推論
- `perp_pos=0.02` はログ上の古い値ではなく、private WS positions 由来の実ポジション。
- `funding_off` 分岐は `cancel_all(reason="funding_below_min")` 後に return するため、既存 position を flatten する設計になっていない。
- unhedged guard は `_oms.unhedged_qty` ベースで、positions sync の `delta=0.02` を直接見ないため、今回の片脚 delta では発火しない。
- `perp 0.02 * mid 約2376 = 約47.5 USDT` は `max_position_notional=100` 未満のため、max position flatten も発火しない。
- fill size 0.0 は Bitget futures private fill の数量フィールドが `baseVolume` で、現行 parser が拾えていない可能性が高い。

### 未確定点
- REST positions API での独立確認は未実施。private WS raw positions では実ポジションを確認済み。
- `baseVolume` 欠落修正後の fill parser 再現テストは未実施。
- 自動 flatten / unwind 実装、config 変更、live 再起動、実ポジション決済は未実施。

---

## 2026-05-06 fill parser / funding_off open delta alert 修正

### 観測事実
- `fills.jsonl` の futures fill が `size=0.0` になる原因候補は、現行 `OMS._parse_fill()` が Bitget futures fill の数量候補に `baseVolume` を含めていなかったこと。
- 旧 MVP parser では futures fill quantity に `baseVolume` を読んでいた。
- `perp=0.02` は private WS raw positions で確認済みの実ポジションであり、別途運用判断が必要。

### 実装
- `bot/oms/oms.py`
  - `OMS._parse_fill()` の futures fill size 候補を `baseVolume`, `size`, `fillSz`, `tradeQty`, `tradeSize` に変更。
  - `_extract_fill_size()` を追加し、数量フィールドを候補順に抽出。
  - `_safe_float()` を `Decimal(str(value))` ベースにし、文字列/数値を安全に float 化。
  - size 欠落、変換不能、または `size <= 0` の fill は正常約定として扱わず、`fill_size_parse_error` / `fill_size_missing_or_zero` を risk log に出す。
- `bot/strategy/mm_funding.py`
  - funding gate の `cancel_all(reason="funding_below_min")` は維持。
  - `check_open_delta_while_stopped()` / `log_open_delta_alert()` を追加。
  - `STOPPED` / `funding_off` 中に `abs(spot_pos + perp_pos) > delta_tolerance` の場合、`funding_off_open_delta` を risk log に出す。
  - alert は `spot_pos`, `perp_pos`, `delta`, `delta_tolerance`, `funding_rate`, `mode`, `trigger_reason`, `action=alert_only` を含む。
- `tests/test_fill_parser.py`
  - futures raw fill の `baseVolume="0.02"` が `size=0.02` になることを追加検証。
  - 既存候補 `size` / `fillSz` / `tradeQty` / `tradeSize` が壊れていないことを検証。
  - zero size fill を reject し warning log を出すことを検証。
- `tests/test_phase_d_strategy.py`
  - `funding_off` 中に `spot=0.0` / `perp=0.02` の場合、flatten せず `funding_off_open_delta` risk log だけ出ることを検証。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\oms\oms.py bot\strategy\mm_funding.py`: pass。
- `.venv\Scripts\python.exe -m pytest tests\test_fill_parser.py tests\test_phase_d_strategy.py -q`: 5 passed。
- `.venv\Scripts\python.exe -m pytest -q`: 57 passed。

### 未確定点
- live 再起動は未実施のため、稼働中プロセスには未反映。
- 自動 flatten / reduceOnly unwind / `funding_off_flatten_enabled` は未実装。
- `config.yaml` は今回変更していない。
- 実ポジション `perp=0.02` の決済は未実施。

---

## 2026-05-06 CI import error 修正

### 観測事実
- GitHub Actions で `bot.oms.oms` import 時に `format_price_for_bitget` / `get_price_tick` / `quantize_perp_price` が `bot.exchange.constraints` に存在しないため collection error。
- ローカルでは `bot/exchange/constraints.py` が未commit変更として残っていたため pytest が通っていた。
- `config.yaml` は引き続き未commit変更であり、今回の修正対象外。

### 実装
- `bot/exchange/constraints.py`
  - `InstrumentConstraints.price_place` を追加。
  - `get_price_tick()` / `quantize_perp_price()` / `format_price_for_bitget()` を追加。
- `tests/test_perp_price_rounding.py`
  - pricePlace 優先、buy 下丸め、sell 上丸め、payload 文字列 formatting を検証。

### 検証
- `.venv\Scripts\python.exe -m pytest -q`: 57 passed。

### 未確定点
- live 再起動、config 変更、実ポジション決済は未実施。

---

## 2026-05-08 SPOT fill price/fee parser と 43012 診断ログ修正

### 観測事実
- `logs/fills.jsonl` 上、内部 `spot_pos=0.04` は SPOT buy fill 3 件合計 `0.06` と SPOT flatten sell fill 2 件合計 `0.02` の差分。
  - `HEDGE-1778083943485-bef13a738b` buy `0.02`
  - `HEDGE-1778116032566-63a1dc51a1` buy `0.02`
  - `FLATTEN-284410-acf781b9d5` sell `0.0153` + `0.0047`
  - `HEDGE-1778116030928-d738caf32b` buy `0.02`
- 2026-05-07 11:15 JST 付近の SPOT flatten sell は `resp_code=43012` で reject。Bitget Spot REST error code 上は Insufficient balance として扱う。
- 現行 private WS subscription は `orders` / `fill` / futures `positions` のみで、SPOT balance sync は無い。
- 既存 `OMS._parse_fill()` は SPOT fill price 欠落時も `price=0.0` の正常 fill として扱っていた。
- `fills.jsonl` の futures / spot fee は全件 `0.0`。既存 parser は `feeDetail` を読まず、PnL 側も HEDGE / FLATTEN / UNWIND fill の fee 計上前に return していた。

### 推論
- 43012 は内部 `spot_pos` と実口座 available ETH の不一致、または遅延/別 tradeId の SPOT fill 反映順により、内部が売却可能残高を過大評価した可能性が高い。
- `price=0.0` の SPOT fill は hedge slip / gross spread / PnL 評価を壊す穴だった。
- SPOT fill dedupe は `SPOT:tradeId` で機能するが、同じ clientOid でも異なる tradeId の遅延 fill は重複扱いにならない。

### 実装
- `bot/oms/oms.py`
  - `_extract_fill_price()` を追加し、SPOT は `priceAvg` / `fillPrice` / `tradePrice` / `price` / `px` を候補順に抽出。
  - price 欠落または `price <= 0` の fill は正常 fill として扱わず、`risk` / `reason=fill_parse_warning` / `parse_reason=fill_price_missing_or_invalid` を出す。
  - warning に `inst_type`、raw keys、`order_id`、`trade_id`、`client_oid` を含める。
  - `_extract_fill_fee()` を追加し、`fee` / `fillFee` / `transactionFee` / `totalFee` と JSON 文字列または list/dict の `feeDetail` を抽出。
  - PnL fee 計上を全 fill 共通に移動し、HEDGE / FLATTEN / UNWIND でも `fees_paid` に反映されるように変更。
  - SPOT sell flatten 前に store から available base coin を read-only で探す precheck warning を追加。現状は balance store が無い場合 `spot_flatten_available_precheck_unavailable` で `action=warn_only`。
  - order reject risk log に `inst_type` / `symbol` / `side` / `size` / `response_msg` / `spot_pos_internal` / `perp_pos_internal` / `delta` / `spot_available` を追加。SPOT FLATTEN の `43012` は `reject_detail=spot_flatten_insufficient_balance`。
- `tests/test_fill_parser.py`
  - SPOT `priceAvg`、`fillPrice`、`tradePrice`、`price`、`px` の price 抽出テストを追加。
  - price 欠落 fill が reject され warning log を出すテストを追加。
  - `feeDetail` 抽出テストを追加。
  - 既存 futures `baseVolume` テストは維持。

### 検証
- `.venv\Scripts\python -m py_compile bot\oms\oms.py tests\test_fill_parser.py`: pass。
- `.venv\Scripts\python -m pytest tests/test_fill_parser.py -q`: 7 passed。
- `.venv\Scripts\python -m pytest -q`: 61 passed。

### 未確定点
- SPOT available ETH を実際に同期する経路は未実装。次段では private balance channel または read-only REST `get_spot_available_balance(base_coin)` を追加し、`available < sell_size` の場合は `order_new` を出さず `order_skip` / `risk` にする案が必要。
- 稼働中 live process への反映は未実施。live 再起動、config 変更、実ポジション決済は未実施。

---

## 2026-05-08 SPOT flatten available blocking precheck 追加

### 観測事実
- 前回修正時点では SPOT sell flatten 前の available precheck は warning のみで、available 不足を検知しても `_submit_order()` に進む構造だった。
- `43012` は Bitget Spot REST error code 上の Insufficient balance として扱う。

### 実装
- `bot/exchange/bitget_gateway.py`
  - `get_spot_available_balance(base_coin)` を追加。
  - `/api/v2/spot/account/assets` の `data` から `coin` が一致する行を探し、`available` / `availableBalance` / `availableAmount` / `free` / `normalBalance` を候補として抽出。
- `bot/oms/oms.py`
  - `_precheck_spot_flatten_available()` を追加。
  - SPOT `FLATTEN` sell の `client_oid` 生成後、`order_new` 前に available base coin を確認。
  - `available < sell_size` の場合は live 注文を出さず、`event=order_skip` / `reason=spot_flatten_insufficient_available_precheck` / `state=blocked_precheck` を出して停止。
  - available 取得不能時は `reason=spot_flatten_available_precheck_unavailable` の warning を出し、既存挙動維持として `_submit_order()` に進む。
  - ログには `intent` / `inst_type` / `symbol` / `side` / `sell_size` / `spot_available` / `spot_pos_internal` / `perp_pos_internal` / `delta` / `client_oid` / `cycle_id` を含める。
- `tests/test_spot_balance_precheck.py`
  - `available=0.0` / `sell_size=0.04` で `order_new` なし、`order_skip` になることを検証。
  - `available=0.02` / `sell_size=0.04` で `order_new` なし、`order_skip` になることを検証。
  - `available=0.05` / `sell_size=0.04` で従来どおり submit に進むことを検証。
  - available 取得不能時は warning を出し、従来どおり submit に進むことを検証。

### 検証
- `.venv\Scripts\python -m py_compile bot\oms\oms.py bot\exchange\bitget_gateway.py tests\test_spot_balance_precheck.py`: pass。
- `.venv\Scripts\python -m pytest tests/test_spot_balance_precheck.py -q`: 4 passed。
- `.venv\Scripts\python -m pytest -q`: 65 passed。

### 未確定点
- live 再起動は未実施のため、稼働中プロセスには未反映。
- 実ポジション決済、config 変更、funding gate 変更、quote churn 対策、自動 flatten policy 追加は未実施。

---

## 2026-05-08 SPOT fee 建て accounting / startup reconciliation 追加

### 観測事実
- read-only REST 確認で SPOT ETH available は `0.039940000718 ETH`。
- read-only REST 確認で Futures `ETHUSDT` position は `0.0`、open orders は spot/perp とも 0 件。
- bot 内部最終 state は `HALTED`、内部 position は `spot=0.04` / `perp=0.0` / `delta=0.04`。
- 実 available と内部 `spot=0.04` の差分 `0.000059999282 ETH` は、SPOT buy 合計 `0.06 ETH` に対する base coin fee `0.00006 ETH` と整合する可能性が高い。

### 実装
- `bot/types.py`
  - `ExecutionEvent.fee_coin` を optional field として追加。
- `bot/oms/oms.py`
  - `_extract_fill_fee()` を fee amount と fee coin の抽出に拡張。
  - top-level `feeCoin` / `feeCurrency` / `feeCcy` 系と、`feeDetail` 内の `feeCoin` / `feeCurrency` / `feeCcy` / `coin` / `currency` を候補として読む。
  - `_spot_position_delta_after_fee()` を追加し、SPOT buy で fee coin が base coin の場合は `size - abs(fee)` を spot_pos に反映。
  - SPOT sell で fee coin が quote coin の場合は従来どおり `-size` を反映。
  - SPOT fee が非ゼロで fee coin 不明の場合は `fill_parse_warning` / `parse_reason=spot_fee_coin_missing` を raw keys 付きで出し、position は従来どおり size で処理。
  - `reconcile_startup_spot_balance()` を追加し、起動時に `get_spot_available_balance(base_coin)` と内部 `spot_pos` を比較。
  - live で `actual_spot_available` が tolerance 超過かつ内部が flat なら `startup_open_spot_balance_detected` を risk log に出し、`risk.halt()` で quote 開始前に HALTED 側へ倒す。自動補正・決済はしない。
- `bot/app.py`
  - live private 起動時、pos mode 確認後 / funding preflight 前に startup SPOT balance reconciliation を実行。
- `tests/test_fill_parser.py`
  - SPOT buy `size=0.06` / `fee=0.00006 ETH` で `spot_pos=0.05994` になることを検証。
  - その後 SPOT sell `size=0.02` / fee coin `USDT` で `spot_pos=0.03994` になることを検証。
  - fee coin `USDT` の SPOT buy は `spot_pos=size` のままになることを検証。
  - fee coin 不明時に warning が出て、position は従来どおり size で処理されることを検証。
- `tests/test_startup_reconciliation.py`
  - startup actual available `0.03994` / internal `0.0` の live 起動で `startup_open_spot_balance_detected` を出して risk halt することを検証。
  - dry_run では warning のみにすることを検証。
  - internal `0.04` / actual `0.039940000718` は tolerance 内として reconciled になることを検証。

### 検証
- `.venv\Scripts\python -m py_compile bot\types.py bot\oms\oms.py bot\app.py bot\exchange\bitget_gateway.py tests\test_fill_parser.py tests\test_startup_reconciliation.py`: pass。
- `.venv\Scripts\python -m pytest tests/test_fill_parser.py tests/test_startup_reconciliation.py tests/test_spot_balance_precheck.py -q`: 18 passed。
- `.venv\Scripts\python -m pytest -q`: 72 passed。

### 未確定点
- live 再起動は未実施のため、稼働中プロセスには未反映。
- 実ポジション決済、config 変更、funding gate 変更、自動 flatten policy 追加、quote churn 対策は未実施。

---

## 2026-05-08 run_real_logs 既定起動コマンド追加

### 観測事実
- `scripts\run_real_logs.ps1` は引数も `REAL_LOG_CMD` も無い場合、`env 'REAL_LOG_CMD' is required` で停止していた。
- 実稼働前の標準起動手順として wrapper を使う設計だが、毎回 `REAL_LOG_CMD` を手動設定する必要があり運用ミスの原因になっていた。

### 実装
- `scripts/run_real_logs.ps1`
  - 引数なし、かつ `REAL_LOG_CMD` 未設定の場合、既定で `.\.venv\Scripts\python.exe -m bot.app --config config.yaml` を実行するように変更。
  - `DRY_RUN=0` 時の `REAL_RUN_OK=1` 必須ガードは維持。
- `scripts/run_real_logs.sh`
  - 同様に `REAL_LOG_CMD` 未設定時の既定コマンドを追加。

### 検証
- `powershell -NoProfile -Command "\`$null = [scriptblock]::Create((Get-Content scripts/run_real_logs.ps1 -Raw)); 'ps1 syntax ok'"`: pass。
- `.venv\Scripts\python -m pytest -q`: 72 passed。
- `bash -n scripts/run_real_logs.sh`: Windows 環境に `bash` が無いため未実施。

### 未確定点
- live 再起動は未実施。
- 実ポジション決済、注文キャンセル、config 変更は未実施。

---

## 2026-05-08 live bounded shutdown cancel_all 修正

### 観測事実
- live bounded 90 秒実行 `runtime_logs\live_check_20260508_184108` で `order_new=64` / `order_cancel=62` となり、終了後に Futures quote が 1 件残留した。
- 残留した注文は `ETHUSDT` Futures `QUOTE_ASK-281-40cde8a80a` / sell `0.02` / `2288.95`。
- 対象注文は手動 cancel 済み。
- Futures `+0.02 long` も手動決済済みとされ、その後 read-only API で Futures position `0.0`、Futures open orders `0`、SPOT open orders `0`、SPOT ETH available `0.000440000718` を確認。
- 原因候補は `scripts/run_bot_for_duration.py` が timeout 時に Windows の `proc.terminate()` で bot を終了し、bot.app 側の shutdown cancel_all を待てていなかったこと。

### 実装
- `scripts/run_bot_for_duration.py`
  - timeout 時の `terminate` 直行を廃止。
  - Windows では `CREATE_NEW_PROCESS_GROUP` で子プロセスを起動し、timeout 時は `CTRL_BREAK_EVENT` で graceful shutdown を要求。
  - POSIX では `SIGINT` で graceful shutdown を要求。
  - 一定時間待っても終了しない場合のみ kill し、`bounded_graceful_shutdown_timeout` を出して exit code `1` にする。
- `bot/app.py`
  - shutdown helper `_cancel_all_on_shutdown` を追加。
  - shutdown 時に task を cancel してから `oms.cancel_all(reason="shutdown_cancel_all")` を実行。
  - `shutdown_cancel_all_start` / `shutdown_cancel_all_done` / `shutdown_cancel_all_failed` をログ出力。
  - shutdown cancel_all 失敗時は `SystemExit(1)`。
- `tests/test_graceful_shutdown.py`
  - bounded runner が timeout 時に graceful shutdown を要求し、成功時は kill しないことを検証。
  - graceful shutdown timeout 時のみ kill し、non-zero exit になることを検証。
  - shutdown cancel_all 成功/失敗ログを検証。

### 検証
- `.venv\Scripts\python -m py_compile scripts\run_bot_for_duration.py bot\app.py tests\test_graceful_shutdown.py`: pass。
- `.venv\Scripts\python -m pytest tests/test_graceful_shutdown.py -q`: 4 passed。
- `.venv\Scripts\python -m pytest -q`: 76 passed。

### 未確定点
- live 再起動は未実施。
- 今回修正後の live bounded 再実行は未実施。
- 実ポジション決済、実注文、注文キャンセル、config 変更、quote churn 対策は未実施。

---

## 2026-05-08 DRY graceful bounded 再確認

### 観測事実
- 1 回目の DRY bounded run `runtime_logs\dry_graceful_check_20260508_231010` は `CTRL_BREAK_EVENT` 後に exit code `1` となり、`shutdown_cancel_all_start` / `shutdown_cancel_all_done` が出なかった。
- 原因は Windows の `CTRL_BREAK_EVENT` が bot.app 内の asyncio shutdown path に変換されず、shutdown cancel_all の `finally` に到達していないこと。
- 修正後の DRY bounded run `runtime_logs\dry_graceful_check_20260508_231255` は exit code `0`。
- `startup_cancel_all_done=1`、`startup_cancel_all_failed=0`。
- `startup_open_spot_balance_detected=0`、`HALTED=0`。
- `shutdown_signal=1`、`shutdown_requested=1`。
- `shutdown_cancel_all_start=1`、`shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`。
- `book_rx_rate=1`、`msgs=1141` / `msgs_per_sec=19.014` / `avg_levels=53.529`。
- `order_new=108`、すべて `state=dry_run`。
- 最終 active dry-run quote 2 件は `reason=shutdown_cancel_all` で cancel 記録済み。

### 実装
- `bot/app.py`
  - `SIGINT` / `SIGBREAK` / `SIGTERM` を `asyncio.Event` に変換する shutdown signal handler を追加。
  - signal 受信時に `shutdown_signal` / `shutdown_requested` を出し、通常の shutdown cancel_all path に入るようにした。
  - shutdown 後に signal handler を復元する処理を追加。

### 検証
- `.venv\Scripts\python -m py_compile bot\app.py scripts\run_bot_for_duration.py`: pass。
- `.venv\Scripts\python -m pytest tests/test_graceful_shutdown.py -q`: 4 passed。
- `.venv\Scripts\python -m pytest -q`: 76 passed。
- `DRY_RUN=1` / `BOT_MODE=dry` / `scripts/run_bot_for_duration.py --duration-sec 90 --config config.yaml`: exit code `0`。

### 未確定点
- live 再起動は未実施。
- live bounded 再確認は未実施。
- 実ポジション決済、実注文、注文キャンセル、config 変更、quote churn 対策は未実施。

---

## 2026-05-08 DRY_RUN=1 bounded 起動確認

### 観測事実
- `RUN_ID=dry_check_20260508_183141` / `LOG_DIR=runtime_logs\dry_check_20260508_183141` で 90 秒 bounded run を実施。
- startup flags は `env_DRY_RUN=1`、`dry_run=true`、`private_enabled=true`。
- `startup_cancel_all_done` は 1 件。
- `startup_cancel_all_failed` は 0 件。
- `constraints_loaded` は `spot_ready=true` / `perp_ready=true`。
- `startup_open_spot_balance_detected` は 0 件。HALTED も 0 件。
- `book_rx_rate` は 1 件、`msgs=1090` / `msgs_per_sec=18.151` / `avg_levels=27.305`。
- dry_run quote order_new は 48 件、QUOTING tick は 319 件。

### 検証
- `scripts/run_real_logs.ps1` 経由の bounded DRY_RUN=1 起動が完了し、`scripts/run_bot_for_duration.py` により 90 秒で自動停止。

### 未確定点
- live 再起動は未実施。
- 実ポジション決済、実注文、注文キャンセル、config 変更は未実施。

---

## 2026-05-08 run_real_logs 多重起動 self-match 修正

### 観測事実
- `REAL_LOG_CMD` を設定して `scripts\run_real_logs.ps1` を呼ぶと、`Assert-SingleInstance` が呼び出し元 PowerShell の command line に含まれる同一文字列を拾い、`already running` と誤判定した。

### 実装
- `scripts/run_real_logs.ps1`
  - 多重起動判定から現在プロセスと親プロセスを除外。
  - 別プロセスで同一 command が走っている場合だけ拒否する。

### 検証
- DRY_RUN=1 bounded run で確認予定。

### 未確定点
- live 再起動は未実施。
- 実ポジション決済、注文キャンセル、config 変更は未実施。

---

## 2026-05-09 hedge ticket / flatten race 修正

### 観測事実
- `runtime_logs\live_churn_test_20260509_004454` で、USDT-FUTURES `QUOTE_ASK` sell fill 後に SPOT hedge ticket が `OPEN` のまま、同一 cycle で `unhedged_exceeded` による Futures `FLATTEN` buy が走った。
- Futures は flat に戻った後も `process_hedge_tickets` が `hedge_chase` を継続し、SPOT `HEDGE` buy が約定した。
- 結果として `spot_pos=0.01998` / `perp_pos=0.0` 相当の SPOT 残が発生した。

### 推論
- 主因は、`unhedged_exceeded` が OPEN hedge ticket の期限内完了待ちを考慮せず flatten を開始したこと。
- さらに、flatten 開始時に OPEN hedge ticket を fail していなかったため、flatten 後も hedge chase が継続した。

### 実装
- `bot/oms/oms.py`
  - `HedgeTicketSnapshot` を追加。
  - `has_open_hedge_ticket()` / `open_hedge_ticket_snapshot()` / `should_defer_flatten_for_hedge_ticket()` を追加。
  - `flatten()` 開始時に `fail_open_tickets("flatten_started")` を呼び、以後の `hedge_chase` を止めるようにした。
- `bot/strategy/mm_funding.py`
  - `unhedged_exceeded` 発火時、OPEN hedge ticket が期限内なら flatten を出さず、`cancel_all(reason="unhedged_exceeded_deferred_for_hedge_ticket")` に留めるようにした。
  - `unhedged_qty` / `unhedged_notional` / hedge ticket 情報 / positions / `action_taken` を risk log に追加。
- `tests/test_hedge_ticket_flatten_race.py`
  - 期限前 ticket では flatten を defer し active quote cancel に留めることを追加。
  - 期限後 ticket では flatten に進むことを追加。
  - flatten 開始時に OPEN ticket が `ticket_failed` になり、その後 `hedge_chase` / SPOT `HEDGE` order_new が出ないことを追加。

### 検証
- `.venv\Scripts\python -m pytest tests\test_hedge_ticket_flatten_race.py -q`: 3 passed。
- `.venv\Scripts\python -m pytest -q`: 79 passed。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 修正後の live bounded 再確認は未実施。
- live 起動、実ポジション操作、注文キャンセル、config.yaml 変更は未実施。

---

## 2026-05-09 live config churn 低減反映

### 観測事実
- `config.churn_test.yaml` で DRY / live bounded 検証を行い、旧設定より order churn が低下した。
- race 修正後の `runtime_logs\live_30min_after_hedge_race_fix_20260509_023451` は `HALTED=0` / `order_reject=0` / `shutdown_cancel_all_done=1` / `fill_parse_warning=0`。
- 同 30 分 run で PERP fill と SPOT hedge fill が出たが、`unhedged_exceeded_deferred_for_hedge_ticket` により flatten は defer され、`hedge_chase=0` / `FLATTEN order_new=0`。
- 実口座は手動で Futures short と SPOT long を解消し、Futures flat / SPOT dust の状態まで確認済み。

### 実装
- `config.yaml`
  - `strategy.reprice_threshold_bps: 1.0` から `2.5` へ変更。
  - `strategy.quote_refresh_ms: 250` から `500` へ変更。

### 検証
- `git diff -- config.yaml`: 上記 2 項目のみ差分。
- `.venv\Scripts\python -m pytest -q`: 79 passed。

### 未確定点
- 常駐起動は未実施。
- config 反映後の live 常駐確認は未実施。

---

## 2026-05-10 live_watch_20260509_220735 HALTED 対応

### 観測事実
- `runtime_logs\live_watch_20260509_220735` は 60 分監視中に `HALTED=1` / `order_reject=7` / `fills=2` / `ticket_open=1` / `ticket_done=0` となった。
- `QUOTE_BID` futures buy 0.02 fill 後、SPOT `HEDGE` sell 0.02 が `43012 Insufficient balance` で 2 回 reject された。
- `UNWIND` futures sell 0.02 は約定し、read-only 確認では SPOT/Futures とも open orders なし、Futures position なし、SPOT は dust のみだった。
- ただし BOT 内部最終ログは `HALTED / pos_spot=0.0 / pos_perp=0.02 / delta=0.02` だった。
- `UNWIND` 後に Futures `FLATTEN` sell が `40804` x1 / `22002` x4 で reject された。

### 推論
- `43012` は USDT 不足ではなく、SPOT `HEDGE` sell に必要な ETH available 不足が主因。
- `positions_sync` と USDT-FUTURES fill accounting の二重反映により、internal `perp_pos` が実口座より大きくなった可能性が高い。
- `UNWIND` 約定後も internal `perp_pos` が残ったため、不要な reduce-only `FLATTEN` が連発した可能性が高い。

### 実装
- `bot/oms/oms.py`
  - private positions sync が一度成功した live では、USDT-FUTURES fill による `perp_pos` 加算をスキップし、`positions_sync` を authoritative source とするログを追加。
  - positions sync が無い環境や dry-run では、従来どおり futures fill accounting を fallback として維持。
  - SPOT `HEDGE` sell 前に ETH available precheck を追加し、available < remain の場合は HEDGE order を出さず、`spot_hedge_insufficient_available_precheck` を記録して `UNWIND` に進める。
  - `ticket_failed` ログ出力を helper 化。
  - `flatten()` 直前に futures position store を再同期し、実 position が 0 の場合は `futures_flatten_no_position_after_sync` で `FLATTEN` order を skip する。
  - `UNWIND` futures fill で `unhedged_qty` を解消し、実 position flat 後も strategy が unhedged と見続ける状態を避ける。
- `tests/test_fill_parser.py`
  - positions sync authoritative 時に futures fill accounting が二重加算されないことを追加。
  - positions sync が無い場合は futures fill accounting が fallback として動くことを追加。
  - positions sync authoritative 時の `UNWIND` fill で `unhedged_qty` が 0 に戻ることを追加。
- `tests/test_hedge_ticket_flatten_race.py`
  - SPOT `HEDGE` sell の ETH available 不足で HEDGE order を出さず `UNWIND` に進むことを追加。
  - futures position sync が flat を返す場合、`FLATTEN` order を出さないことを追加。

### 検証
- `.venv\Scripts\python.exe -m pytest tests\test_fill_parser.py tests\test_hedge_ticket_flatten_race.py tests\test_spot_balance_precheck.py -q`: 23 passed。
- `.venv\Scripts\python.exe -m pytest -q`: 84 passed。

### 未確定点
- 修正後の live 起動は未実施。
- 実ポジション操作、実注文、決済、キャンセル、`config.yaml` 変更は未実施。
- `40804` / `22002` を reject_streak 対象外にするかは未判断。今回は position sync + order_skip で連発防止を優先。

---

## 2026-05-11 live watch LOG_DIR 取り違え調査 / 監視ログ追加

### 観測事実
- 異常に見えた `fills=0` / internal `pos_spot=0` / `pos_perp=0` / `delta=0` は、監視対象 LOG_DIR の取り違えが主因だった。
- `runtime_logs\live_watch_after_position_fix_20260511_015257` は Codex 側で起動して停止した別 run で、01:56 台に `shutdown_cancel_all_done` まで到達していた。
- 実約定 run は `runtime_logs\live_watch_after_position_fix_20260511_015906` だった。
- `015906` では Futures `QUOTE_ASK` sell 0.02 fill と SPOT `HEDGE` buy 0.02 fill が `fills.jsonl` に記録されていた。
- `015906` では `ticket_open=1` / `ticket_done=1` / `ticket_failed=0` / `positions_sync=26` / `fill_parse_warning=0` / `40804=0` / `22002=0` / `43012=0`。
- `015906` の internal position は `pos_spot=0.01998` / `pos_perp=-0.02` / `delta≈-0.00002` に反映されていた。

### 推論
- 当該 run では private WS fill channel、`store.fill.find()`、SPOT hedge、ticket_done、positions sync は機能していた。
- ただし LOG_DIR と RUN_ID の識別ログ、fill monitor heartbeat、positions monitor heartbeat が無く、監視時の取り違えや monitor 生存確認の切り分けが弱かった。

### 実装
- `scripts/run_real_logs.ps1`
  - wrapper で生成した `RUN_ID` / `GIT_SHA` / 実効コマンドを bot 子プロセスの環境変数へ渡すようにした。
- `bot/app.py`
  - 起動直後に `runtime_log_dir_identity` を `system.jsonl` へ記録するようにした。
  - ログ項目: `LOG_DIR` / `RUN_ID` / `git_sha` / `pid` / `ppid` / `cmd` / `config_path` / `dry_run` / `bot_mode`。
- `bot/oms/oms.py`
  - `monitor_fills()` に `fill_monitor_heartbeat` を追加した。
  - ログ項目: `store_fill_count` / `seen_fill_count` / `last_fill_ts` / `last_fill_id` / `parse_warning_count` / `monitor_exception` / `task_alive`。
  - `sync_positions()` 周辺に `positions_monitor_heartbeat` を追加した。
  - ログ項目: `store_positions_count` / `parsed_perp_pos` / `positions_sync_authoritative` / `last_positions_sync_ts` / `monitor_exception` / `task_alive`。

### 未確定点
- 追加ログの live 実運用での実出力確認は未実施。
- live 起動、注文、決済、キャンセル、`config.yaml` 変更は未実施。

---

## 2026-05-12 live watch HALTED / stale internal perp_pos 修正

### 観測事実
- 対象 LOG_DIR: `runtime_logs\live_watch_monitor_identity_20260511_224417`
- 30分監視で BOT は `HALTED`、latest internal は `pos_spot=0.0` / `pos_perp=0.02` / `delta=0.02`。
- read-only 実口座確認では Futures position=0 / open orders=0 / SPOT dust のみ。
- UNWIND fill と FLATTEN fill は `order_id` / `fill_id` / `client_oid` / `ts` が異なる別 fill で、dedupe 主因ではない。
- positions sync の最後は `pos_perp=0.02`。その後 positions store が空になったが、internal `pos_perp` が 0 に更新されず stale に残った。
- stale `pos_perp=0.02` により FLATTEN が繰り返され、reduce-only `22002 No position to close` が reject streak を進めた。

### 推論
- 主因は positions store empty を flat として同期できなかったこと。
- futures fill accounting は `positions_sync_authoritative=true` で skip されており、fill 二重 apply が主因ではない可能性が高い。
- UNWIND 注文/約定待ち中に `unhedged_exceeded` から FLATTEN が追加で走ったことも 22002 再発を増やした。

### 実装
- `bot/oms/oms.py`
  - live かつ positions sync authoritative 済みで positions store が空なら、`positions_store_empty_assume_flat` として internal `perp_pos=0.0` に同期する。
  - `positions_monitor_heartbeat` に `positions_empty` を追加。
  - FLATTEN / UNWIND の reduce-only order が `22002` を返した場合、先に futures position sync を行い、flat 確認できた場合は `reduce_only_no_position_sync_flat` として reject streak に積まない。
  - UNWIND order 成功後に短時間の pending window を持つ。
- `bot/strategy/mm_funding.py`
  - UNWIND pending 中の `unhedged_exceeded` は `unhedged_exceeded_deferred_for_unwind_pending` として active quote cancel のみ行い、追加 FLATTEN を出さない。
- `tests/test_hedge_ticket_flatten_race.py`
  - positions store empty assume flat、reduce-only 22002 sync flat、UNWIND pending 中の FLATTEN defer のテストを追加。

### 未確定点
- live 起動、実ポジション操作、注文、決済、キャンセル、`config.yaml` 変更は未実施。
- 修正後の live bounded 検証は未実施。

---

## 2026-05-12 stop_bot stale pid / graceful stop 修正

### 観測事実
- `runtime_logs\live_watch_after_flat_sync_fix_20260512_193959` の起動直後チェックは正常。
- 起動直後ログでは `runtime_log_dir_identity=1`、`startup_cancel_all_done=1`、`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`book_rx_rate=1`、`fill_monitor_heartbeat=2`、`positions_monitor_heartbeat=2`、`state=QUOTING`、`delta=0.0`。
- `scripts\stop_bot.ps1` は `logs\bot.pid` の古い PID を参照し、実行中の `bot.app` を通常停止できなかった。
- 手動でプロセスツリーを停止したため `shutdown_cancel_all_done` が出ず、Futures quote 2本が残留した。
- 残留 quote 2本は手動キャンセル済み。停止後 read-only では SPOT open orders=0 / Futures open orders=0 / Futures ETHUSDT position=0.0 / SPOT ETH は dust のみ。

### 推論
- 旧 `stop_bot.ps1` は stale pid file に弱く、pid file が実 bot process を指していない場合に fallback 検出できなかった。
- 強制停止に落ちると `bot.app` の shutdown path に入らず、`shutdown_cancel_all` が走らないため quote 残留リスクがある。

### 実装
- `scripts/stop_bot.ps1`
  - stale pid file 判定を追加: PID 不在、PID が `bot.app` でない、wrapper/cmd 配下に `bot.app` がいない場合に `stale_pid_file` を出力。
  - pid file に頼らず `python -m bot.app --config config.yaml` を CommandLine から fallback 検出。
  - `CTRL_BREAK_EVENT` 送信を優先し、`shutdown_cancel_all_done` をログから確認してから終了判定。
  - graceful timeout 時のみ `Stop-Process -Force` に落とし、`forced_stop_used=true` を明示。
  - `-DryRun` を追加し、停止操作なしで検出と出力を確認可能にした。
- `scripts/run_real_logs.ps1`
  - 起動時に stale pid file を判定して上書き可能にした。
  - wrapper/cmd の PID ではなく、子孫の実 `python -m bot.app --config config.yaml` PID を `logs\bot.pid` に保存。
  - `logs\bot.run.json` と run meta に `run_id` / `log_dir` / `git_sha` / `config_path` / `dry_run` / `bot_mode` / `bot_pid` を保存。
- `tests/test_stop_bot_scripts.py`
  - stale pid / fallback 検出、graceful 優先、pid metadata 記録の静的テストを追加。

### 検証
- PowerShell 構文チェック: `scripts\stop_bot.ps1` pass。
- PowerShell 構文チェック: `scripts\run_real_logs.ps1` pass。
- `scripts\stop_bot.ps1 -DryRun -GracefulWaitSec 1`: pass。実停止なし、注文/キャンセルなし。
- `scripts\run_real_logs.ps1` smoke: `DRY_RUN=1` かつ `REAL_LOG_CMD='cmd /c echo smoke_ok'` で pass。live 起動なし、注文/キャンセルなし。

### 未確定点
- 実 live 常駐プロセスに対する `CTRL_BREAK_EVENT` 経由の graceful shutdown 実地確認は未実施。
- 本修正後の live 起動、注文、決済、キャンセル、`config.yaml` 変更は未実施。

---

## 2026-05-17 収益改善設定 / live profitability 分析追加

### 観測事実
- 対象ログ: `runtime_logs\live_watch_after_stop_pid_fix_20260512_202042`
- 稼働時間は約 47.96 時間。
- 約定は 15 件: `USDT-FUTURES:QUOTE_BID=6` / `USDT-FUTURES:QUOTE_ASK=1` / `USDT-FUTURES:UNWIND=6` / `SPOT:HEDGE=2`。
- `order_reject=0`、`resp_code 22002=0`、`fill_parse_warning=0`。
- 実口座 read-only では売却後に SPOT open orders=0 / Futures open orders=0 / Futures position=0.0 / SPOT ETH available=0.000480000718 / SPOT ETH frozen=0.0。
- 旧設定 `base_half_spread_bps=14.0` / `min_half_spread_bps=14.0` では pre quote の `expected_edge_bps` が概ね 0.2bps と薄い。
- ログ上 `tfi_fade_suppressed` が多く、`tfi_fade_policy=disabled` により強い flow 方向の fade が抑制されていた。

### 推論
- spot taker fee 10bps と UNWIND の market/reduce-only 手数料・滑りを考慮すると、0.2bps 程度の expected edge では薄すぎる。
- QUOTE_BID 後に UNWIND で不利価格になるケースが複数あり、強い TFI 方向では quote を遠ざける方が期待損失を下げやすい。
- fill / pnl の再集計を継続的に再現できる分析スクリプトが必要。

### 実装
- `config.yaml`
  - `base_half_spread_bps`: 14.0 -> 18.0
  - `min_half_spread_bps`: 14.0 -> 18.0
  - `tfi_fade_policy`: `disabled` -> `threshold_0p7`
- `scripts/analyze_live_profitability.py`
  - gzip 済みローテーションログを含め、event/reason/block/fill/pnl を集計。
  - quote fill と直後の HEDGE/UNWIND を粗くペアリングし、既知 USDT fee 込みの rough net を算出。
- `tests/test_analyze_live_profitability.py`
  - QUOTE_BID -> UNWIND のペアリングと fee 込み rough net のテストを追加。

### 検証
- `scripts/analyze_live_profitability.py runtime_logs\live_watch_after_stop_pid_fix_20260512_202042`: pass。
- `pytest tests/test_analyze_live_profitability.py tests/test_tfi_fade_policy.py tests/test_pnl_logger.py`: 8 passed。

### 未確定点
- 18bps / `threshold_0p7` 設定での DRY bounded / live bounded 検証は未実施。
- 本変更後の live 起動、注文、決済、キャンセルは未実施。

---

## 2026-05-17 18bps / TFI fade DRY bounded 5分

### 観測事実
- 対象ログ: `runtime_logs\dry_profit_config_18bps_tfi_20260517_203631`
- `DRY_RUN=1` / `BOT_MODE=dry` / duration 300秒で完走。
- `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`startup_open_spot_balance_detected=0`。
- `shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`。
- `book_rx_rate=4`、`fill_monitor_heartbeat=5`、`positions_monitor_heartbeat=5`。
- `positions_monitor_heartbeat` に `positions_empty=true` が出力された。
- `pre_quote_decision` に `base_half_spread_bps=18.0` / `min_half_spread_bps=18.0` が出力された。
- `tfi_fade_suppressed` に `tfi_fade_policy=threshold_0p7` が出力された。
- 対象 repo の `python -m bot.app` 残存プロセスはなし。

### 推論
- 18bps / `threshold_0p7` 設定は DRY 起動・終了 path では破綻していない。

### 未確定点
- 同設定での live bounded 検証は未実施。
- 実約定時の HEDGE / UNWIND / FLATTEN path は未確認。

---

## 2026-05-21 SPOT hedge 価格整形 / BID quote 残高ガード

### 観測事実
- `runtime_logs\live_watch_18bps_tfi_20260518_233747` は停止済み。現在の実口座 read-only は SPOT open orders=0 / Futures open orders=0 / Futures ETHUSDT position=0.0 / SPOT ETH available=0.000420000718 / SPOT ETH frozen=0.0。
- 同ログでは `HALTED=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`、`resp_code_22002=0`。
- 実約定は 10 件: `QUOTE_BID=3` / `QUOTE_ASK=2` / `SPOT:HEDGE=4` / `UNWIND=1`。
- `order_reject=38`、`max_reject_streak=2`。内訳は `41103=1`、`45001=37`。
- `41103` は SPOT HEDGE chase の `price=2105.2400000000002` / `price_payload=null` / `response_msg=param price scale error error` で発生。
- 前回 12時間 run では SPOT available dust 状態で futures BID が約定し、SPOT SELL hedge が `spot_hedge_insufficient_available_precheck` で失敗して UNWIND 損切りになった。

### 推論
- SPOT HEDGE / chase 注文も PERP と同様に Decimal tick 整形済み price payload を使う必要がある。
- SPOT available が futures BID 約定後の SELL hedge 必要量未満なら、BID quote を出すべきではない。

### 実装
- `bot/exchange/constraints.py`
  - `quantize_price_floor` を追加し、SPOT 価格を従来の floor 方針のまま Decimal tick に合わせる。
- `bot/exchange/bitget_gateway.py`
  - SPOT order REST payload の `price` を `format_price_for_bitget` 済み文字列に変更。
- `bot/oms/oms.py`
  - `_submit_order` の SPOT `price_payload` を記録。
  - quote 判定用の SPOT available cache `spot_available_for_quote` を追加。
- `bot/strategy/mm_funding.py`
  - futures BID quote 前に SPOT available を確認し、必要 hedge sell size 未満なら `spot_hedge_sell_available_block` として BID を抑制。
- `tests/test_hedge_ticket_flatten_race.py`
  - SPOT hedge の `price_payload=2105.24` と gateway REST payload 整形を追加検証。
- `tests/test_phase_d_strategy.py`
  - SPOT dust 時に BID quote だけ suppress され、ASK は維持される検証を追加。

### 検証
- `pytest tests/test_hedge_ticket_flatten_race.py tests/test_phase_d_strategy.py tests/test_perp_price_rounding.py`: 16 passed。
- `pytest`: 94 passed。

### 未確定点
- 修正後の live 24時間 forward は未実施。
- `45001 Unknown error` の根本原因は未確定。

---

## 2026-05-25 live 24h 後確認 / SPOT 残数クリア / flatten race P1 修正

### 観測事実
- 対象ログ: `runtime_logs\live_forward_spot_guard_24h_20260523_121024`
- 24h bounded run は停止済み。`bot.app` 残存プロセスなし。
- `shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`、`forced_stop_used=0`。
- `HALTED=0`、`fill_parse_warning=0`、`resp_code 22002=0`。
- `order_reject=5`。全件 `SPOT FLATTEN sell` の `40808 size checkBDScale`。
- fill 時系列では `05:44:43 QUOTE_ASK sell futures 0.02` 後、`05:44:44 FLATTEN buy futures 0.06` が先行し、`05:44:54 SPOT HEDGE buy 0.02` が遅れて約定。
- 終端内部状態は `spot_pos=0.059899999999999995` / `perp_pos=0.0` / `delta=0.059899999999999995`。
- read-only 実口座確認では SPOT open orders=0 / Futures open orders=0 / Futures position=0 / SPOT ETH available=0.060300000718 / frozen=0。
- 手動残数クリア: SPOT market sell `0.0603`、orderId `1442720056529858561`、response `00000`。
- クリア後 read-only: SPOT open orders=0 / Futures open orders=0 / Futures position=0 / SPOT ETH available=0.000000000718 / frozen=0。

### 推論
- 未完了 `HEDGE` pending 中に `FLATTEN` が走り、futures 側だけ先に 0 へ戻した後、遅延 SPOT HEDGE が約定して SPOT 単独残りになった。
- SPOT FLATTEN size が `0.039900000000000005` の raw float 文字列で送信され、Bitget の size precision 制約で reject された。

### 実装
- `bot/exchange/constraints.py`
  - `quantize_size_floor` / `format_size_for_bitget` を追加。
- `bot/exchange/bitget_gateway.py`
  - SPOT / Futures order payload の `size` を constraints の `qty_step` に floor quantize して送信。
- `bot/oms/oms.py`
  - 未期限 `HEDGE` ticket または `UNWIND pending` 中の `flatten` を OMS 内でも defer。
  - defer 時は quote cancel のみ実行し、`flatten_deferred_for_hedge_ticket` / `flatten_deferred_for_unwind_pending` を記録。
- `tests/test_perp_price_rounding.py`
  - size quantize / payload 検証を追加。
- `tests/test_hedge_ticket_flatten_race.py`
  - direct `OMS.flatten` が hedge pending 中に注文せず defer する検証を追加。

### 検証
- `pytest tests\test_perp_price_rounding.py tests\test_hedge_ticket_flatten_race.py tests\test_spot_balance_precheck.py`: 21 passed。
- `pytest`: 98 passed。
- 修正後 read-only: SPOT open orders=0 / Futures open orders=0 / Futures position=0 / SPOT ETH available=0.000000000718 / frozen=0。

### 未確定点
- 修正後の DRY / live bounded forward は未実施。

---

## 2026-05-27 SPOT fee 後の partial BID exit 修正

### 観測事実
- 対象ログ: `runtime_logs\live_forward_flatten_defer_size_quant_24h_20260525_172423`
- 24h bounded run は完走。`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`。
- 約定は `USDT-FUTURES:QUOTE_ASK sell 0.02` と `SPOT:HEDGE buy 0.02` の 2 件。
- `QUOTE_ASK orders=11456`、`QUOTE_BID orders=0`。
- HEDGE buy 後の SPOT available は `0.019980000718`。BID hedge sell required は `0.02`。
- `spot_hedge_sell_available_block` により BID exit が抑制され、ヘッジ carry の回転が止まった。
- 手動 flat 実施済み。read-only では SPOT open orders=0 / Futures open orders=0 / Futures position=0 / SPOT ETH dust。

### 推論
- SPOT HEDGE buy の手数料が ETH 払いのため、満額 `0.02` を買っても available は `0.01998` になる。
- 満額 `0.02` の BID だけを許可する設計では、fee shortfall により exit/rebalance BID が出ない。

### 実装
- `bot/strategy/mm_funding.py`
  - SPOT available が required を下回る場合、即 BID suppress せず、available を futures qty step に合わせて floor した partial BID size を許可。
  - partial 化できない dust の場合は従来どおり `spot_hedge_sell_available_block`。
  - `spot_hedge_sell_available_reduce` と `spot_hedge_sell_adjusted` をログ追加。
- `tests/test_phase_d_strategy.py`
  - dust では BID suppress 継続。
  - `available=0.019980000718` では `bid_size=0.01` に縮小して BID を出す検証を追加。

### 検証
- `pytest tests\test_phase_d_strategy.py tests\test_one_sided_quote_policy.py tests\test_total_edge.py tests\test_hedge_ticket_flatten_race.py`: 18 passed。
- `pytest`: 99 passed。
- 起動前 read-only: SPOT open orders=0 / Futures open orders=0 / Futures position=0 / SPOT ETH available=0.000080000718 / frozen=0。

### 未確定点
- 修正後の live 24h forward はこれから実施。

---

## 2026-05-30 live 24h 後分析 / FLATTENING dust 復帰修正

### 観測事実
- 対象ログ: `runtime_logs\live_forward_partial_bid_exit_24h_20260527_170927`
- 24h bounded run は終了済み。`shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`、`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`。
- `bot.app` は残存なし。`run_real_logs.ps1` wrapper `powershell.exe` が単独残留。
- 約定は `QUOTE_ASK sell 0.04`、`SPOT HEDGE buy 0.06`、`FUTURES FLATTEN buy 0.04`、`SPOT FLATTEN sell 0.0599`。
- 終端内部状態は `spot_pos=0.00004` / `perp_pos=0.0` / `unhedged_qty=-0.02`。
- `spot_flatten_available_precheck` と `FLATTEN order_skip size=0` が大量発生。
- `SPOT HEDGE` chase が、先行 spot hedge order の fill 到着前に追加発注され、過剰 hedge になった。
- `resp_code 22002` は発生したが、`order_reject=0` で reject streak には積まれていない。

### 推論
- 実口座は flat/dust だが、内部 `unhedged_qty` が FLATTEN 後に残り、`FLATTENING` 復帰不能になった。
- `HEDGE` ticket が pending spot order 数量を持たず、未約定注文がある状態で chase を重ねた。
- wrapper 単独残留は、子プロセス終了後の明示終了不足が原因候補。

### 実装
- `bot/oms/oms.py`
  - `HedgeTicket.pending_qty` / `pending_order_id` / `unreserved_qty` を追加。
  - pending spot hedge order が残る間は `hedge_chase_deferred_pending_order` として chase を抑止。
  - pending spot hedge order が期限切れなら chase 前に spot cancel を実行。
  - hedge fill 受信時に `pending_qty` を減算。
  - flat/dust かつ open hedge/unwind pending なしの場合に `unhedged_qty` をクリアする `flat_dust_unhedged_cleared` を追加。
- `bot/strategy/mm_funding.py`
  - unhedged 判定前に `clear_unhedged_if_flat_dust` を呼び、FLATTEN 後 dust から `QUOTING` 復帰できるようにした。
- `scripts/run_real_logs.ps1`
  - child process 終了後に `WaitForExit()` / `Dispose()` / `exit 0` を明示。
- `tests/test_hedge_ticket_flatten_race.py`
  - pending spot hedge order 中に chase しない検証を追加。
  - pending spot hedge order 期限切れ時に chase 前 cancel する検証を追加。
  - flat/dust で stale `unhedged_qty` をクリアする検証を追加。
- `tests/test_stop_bot_scripts.py`
  - wrapper 明示終了の文字列検証を追加。

### 検証
- `pytest tests\test_hedge_ticket_flatten_race.py tests\test_stop_bot_scripts.py`: 17 passed。
- `pytest`: 102 passed。
- PowerShell syntax check: `scripts\run_real_logs.ps1` / `scripts\stop_bot.ps1` OK。
- `config.yaml` 差分なし。

### 未確定点
- 修正後の DRY / live forward は未実施。

---

## 2026-05-31 24h live 収益化分析 / half spread 最小調整

### 観測事実
- 対象ログ: `runtime_logs\live_forward_hedge_pending_flat_dust_24h_20260530_011727`
- 24h bounded run は完走。`shutdown_cancel_all_done=1`、`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`22002=0`。
- `fill_count=0`、`pnl_net_sum=0.0`。
- `QUOTE_ASK order_new=12044`、`QUOTE_BID order_new=0`。
- `spot_hedge_sell_available_block=158905`。SPOT available は `0.000120000718`、required は `0.02`。
- active ASK と買い約定価格の距離は p50 約 `17.23bps`、最小 約 `5.99bps`。
- active ASK と best ask の距離は p50 約 `17.22bps`。

### 推論
- 安全面は合格だが、収益化は fill 0 のため不合格。
- BID は SPOT dust のため設計通り停止。初回 entry は ASK 側に依存。
- 現行 `18bps` half spread は 24h で約定しない水準。
- `16bps` なら概算 expected edge はプラスを保ちつつ、約定候補が増える可能性がある。

### 実装
- `config.yaml`
  - `strategy.base_half_spread_bps`: `18.0 -> 16.0`
  - `strategy.min_half_spread_bps`: `18.0 -> 16.0`

### 検証
- `pytest`: 102 passed。
- `load_config('config.yaml')`: `base_half_spread_bps=16.0` / `min_half_spread_bps=16.0`。

### 未確定点
- `16bps` 設定での DRY / short live fill率は未確認。
- 実約定後の `HEDGE pending` / `flat_dust_unhedged_cleared` パスは未確認。

---

## 2026-06-01 16bps live 15分 / tfi_fade 無効化

### 観測事実
- 対象ログ: `runtime_logs\live_forward_16bps_fill_discovery_15min_20260601_034218`
- 15分 bounded run は完走。`shutdown_cancel_all_done=1`、`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`。
- `fill_count=0`、`pnl_net_sum=0.0`。
- `QUOTE_ASK order_new=125`、`QUOTE_BID order_new=0`。
- `spot_hedge_sell_available_block=1671`。
- `tfi_fade=1394`。内訳は `ask=886` / `bid=508`。
- TFI は飽和気味。`abs(tfi)>=0.7` が `1440/1721`。
- active ASK と買い約定価格の距離は p50 約 `30.91bps`。TFI fade によりASKが遠くなる時間が多い。

### 推論
- `16bps` でも fill が出ない主因候補は、半分以上の時間で TFI fade がASKを追加で遠ざけていること。
- TFI が飽和気味のため、現状の `threshold_0p7` はフィルターとして強すぎる。
- half spread をさらに狭める前に、`tfi_fade_policy` を無効化して実約定機会を確認するのが最小変更。

### 実装
- `config.yaml`
  - `strategy.tfi_fade_policy`: `threshold_0p7 -> disabled`

### 検証
- `load_config('config.yaml')`: `base_half_spread_bps=16.0`、`min_half_spread_bps=16.0`、`tfi_fade_policy=disabled`。
- `.venv\Scripts\python.exe -m pytest -q`: `102 passed`。

### 未確定点
- `16bps + tfi_fade disabled` の short live fill率は未確認。
- 実約定後の `HEDGE pending` / `flat_dust_unhedged_cleared` パスは未確認。

---

## 2026-06-01 16bps no_tfi_fade live 15分 / 14bps 調整

### 観測事実
- 対象ログ: `runtime_logs\live_forward_16bps_no_tfi_fade_15min_20260601_040034`
- 15分 bounded run は完走。`shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`、`forced_stop_used=0`。
- `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`resp_code 22002=0`。
- `bot.app` 残存は `0`。
- `fill_count=0`、`pnl_net_sum=0.0`。
- `QUOTE_ASK order_new=78`、`QUOTE_BID order_new=0`。
- `spot_hedge_sell_available_block=1695`。
- `tfi_fade_triggered=False`、`tfi_fade_suppressed=1416`。`tfi_fade_policy=disabled` は反映済み。
- QUOTE_ASK と mid の距離は p50 約 `15.17bps`、p90 約 `16.73bps`。

### 推論
- TFI fade 無効化でASK距離は縮んだが、15分では fill なし。
- 現行コスト式では `14bps` が正エッジをほぼ維持する下限候補。`12bps` 以下は discovery には使えても通常運用前提では負エッジ化する。
- 次は `14bps` で short live fill率を確認する。

### 実装
- `config.yaml`
  - `strategy.base_half_spread_bps`: `16.0 -> 14.0`
  - `strategy.min_half_spread_bps`: `16.0 -> 14.0`

### 検証
- `load_config('config.yaml')`: `base_half_spread_bps=14.0`、`min_half_spread_bps=14.0`、`tfi_fade_policy=disabled`。
- `.venv\Scripts\python.exe -m pytest -q`: `102 passed`。

### 未確定点
- `14bps + tfi_fade disabled` の short live fill率は未確認。
- 実約定後の `HEDGE pending` / `flat_dust_unhedged_cleared` パスは未確認。

---

## 2026-06-01 14bps no_tfi_fade live 15分・60分

### 観測事実
- 15分ログ: `runtime_logs\live_forward_14bps_no_tfi_fade_15min_20260601_041837`
- 60分ログ: `runtime_logs\live_forward_14bps_no_tfi_fade_60min_20260601_043635`
- 15分: `shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`、`forced_stop_used=0`、`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`resp_code 22002=0`。
- 15分: `fill_count=0`、`pnl_net_sum=0.0`、`QUOTE_ASK order_new=102`、`QUOTE_BID order_new=0`。
- 15分: QUOTE_ASK と最新 mid の距離は p50 約 `13.00bps`、p90 約 `14.66bps`。
- 60分: `shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`、`forced_stop_used=0`、`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`resp_code 22002=0`。
- 60分: `fill_count=0`、`pnl_net_sum=0.0`、`QUOTE_ASK order_new=312`、`QUOTE_BID order_new=0`。
- 60分: QUOTE_ASK と最新 mid の距離は p50 約 `13.54bps`、p90 約 `14.85bps`。
- 60分終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures ETHUSDT position=0.0`、`SPOT ETH available=0.000120000718`、`SPOT ETH frozen=0.0`。

### 推論
- 安全面は合格。停止・残留・reject・parse warning は問題なし。
- 収益化は不合格。`14bps` は現行コスト式で正エッジ下限付近だが、60分で fill なし。
- `12bps` 以下に下げると fill discovery は進む可能性があるが、通常運用前提では負エッジ化する。
- 現状の one-sided ASK entry だけでは、正エッジを維持したまま約定を取れていない。

### 実装
- 追加コード変更なし。

### 検証
- `.venv\Scripts\python.exe scripts\analyze_live_profitability.py runtime_logs\live_forward_14bps_no_tfi_fade_60min_20260601_043635`: `fill_count=0`、`pnl_net_sum=0.0`。
- read-only 実口座確認: open orders `0`、Futures position `0.0`、SPOT dust のみ。

### 未確定点
- 実約定後の `HEDGE pending` / `flat_dust_unhedged_cleared` パスは未確認。
- 次の収益化候補は、負エッジ spread への単純縮小ではなく、entry 構造の変更または在庫前提の見直し。

---

## 2026-06-01 SOL/XRP 銘柄変更検証 / private fill 欠落対策

### 観測事実
- `SYMBOL=SOLUSDT` DRY 15分: `runtime_logs\dry_symbol_SOLUSDT_15min_20260601_171138`。
- SOL DRY: `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`。ただし run 中 funding が負で `funding_off`、`order_new=0`。
- Bitget 公開API確認では、候補内で `XRPUSDT` が funding `1.0bps`、perp spread 約 `0.77bps`、24h quote volume 約 `115M`。
- `SYMBOL=XRPUSDT` DRY 15分: `runtime_logs\dry_symbol_XRPUSDT_15min_20260601_172822`。
- XRP DRY: `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`、`QUOTE_ASK order_new=359`。
- XRP live 前 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures XRPUSDT position=0.0`、`SPOT XRP available=0.000046`、`SPOT XRP frozen=0.0`。
- `SYMBOL=XRPUSDT` live 15分: `runtime_logs\live_symbol_XRPUSDT_15min_20260601_174446`。
- XRP live: `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`、`QUOTE_ASK order_new=363`。
- XRP live 後 read-only で `Futures XRPUSDT position=-46.0` を検出。`fills.jsonl` は `fill_count=0`、`fill_monitor_heartbeat.store_fill_count=0`、`positions_monitor_heartbeat.store_positions_count=0`。
- 手動安全復旧: `reduceOnly=YES` の XRPUSDT market buy `46` を送信し、response `code=00000`。
- 復旧後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures XRPUSDT position=0.0`、`SPOT XRP frozen=0.0`。

### 推論
- XRP は ETH より quote 生成密度が高く、銘柄変更の方向性は有効候補。
- ただし private WS の `fill` / `positions` store が空のまま実約定が発生し、bot が hedge できなかった。
- 収益化検証を続ける前に、REST position fallback と未認識 delta の強制 flatten が必須。

### 実装
- `bot/exchange/bitget_gateway.py`
  - read-only REST `get_perp_position()` を追加。
- `bot/oms/oms.py`
  - WS positions store が空の場合、live では REST position fallback で `perp_pos` を同期。
- `bot/strategy/mm_funding.py`
  - `abs(delta) > delta_tolerance` かつ hedge ticket / unhedged がない場合、quote せず `open_delta_without_hedge_ticket` で `flatten`。
- `tests/test_startup_reconciliation.py`
  - WS positions store 空時の REST fallback test を追加。
- `tests/test_hedge_ticket_flatten_race.py`
  - hedge ticket なし open delta の flatten test を追加。

### 検証
- `.venv\Scripts\python.exe -m pytest -q tests\test_startup_reconciliation.py tests\test_hedge_ticket_flatten_race.py`: `19 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `104 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- private WS の fill/positions store が空になる根本原因は未確定。
- 修正後の live 再検証は未実施。次の live は XRPUSDT 15分以下に限定し、REST fallback / `open_delta_without_hedge_ticket` を重点監視する。

---

## 2026-06-01 XRPUSDT 修正後 live 15分 / REST flat 判定補正

### 観測事実
- `SYMBOL=XRPUSDT` live 15分: `runtime_logs\live_symbol_XRPUSDT_after_rest_fallback_15min_20260601_221640`。
- live 15分: `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`resp_code 22002=0`、`shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`。
- `runtime_log_dir_identity=1`、`startup_cancel_all_done=1`、`startup_open_spot_balance_detected=0`。
- `book_rx_rate=14`、`fill_monitor_heartbeat=15`、`positions_monitor_heartbeat=14`。
- `QUOTE_ASK order_new=318`、`order_cancel=318`、実 fill は未発生。
- 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures XRPUSDT position=0.0`、`SPOT XRP available=0.000046`、`SPOT XRP frozen=0.0`。
- Bitget REST `/api/v2/mix/position/single-position` は flat 時に `data=[]` を返す。

### 推論
- 修正後 15分では残留・reject・HALTED は再発していない。
- ただし `data=[]` を `None` 扱いしていたため、flat 時の REST fallback が authoritative sync にならなかった。

### 実装
- `bot/exchange/bitget_gateway.py`
  - empty REST position rows を `0.0` として扱うよう補正。
- `tests/test_startup_reconciliation.py`
  - empty REST position rows が flat になる test を追加。

### 検証
- `.venv\Scripts\python.exe -m pytest -q tests\test_startup_reconciliation.py`: `5 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `105 passed`。
- read-only helper: `get_perp_position=0.0`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 実 fill 発生時の `positions_rest_fallback` / `open_delta_without_hedge_ticket` は未確認。

---

## 2026-06-01 XRPUSDT live 30分 / HEDGE IOC 修正

### 観測事実
- `SYMBOL=XRPUSDT` live 30分: `runtime_logs\live_symbol_XRPUSDT_after_rest_flat_fix_30min_20260601_230228`。
- live 30分: `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`、`shutdown_cancel_all_failed=0`。
- `runtime_log_dir_identity=1`、`startup_cancel_all_done=1`、`startup_open_spot_balance_detected=0`。
- `book_rx_rate=29`、`fill_monitor_heartbeat=30`、`positions_monitor_heartbeat=29`。
- QUOTE_ASK sell fill: `price=1.2857`、`size=46.0`、`fee=-0.0082799`。
- HEDGE spot buy は `force=post_only` で出て約定せず、`ticket_failed fail_reason=flatten_started`。
- FLATTEN futures buy fill: `price=1.2859`、`size=45.0 + 1.0`、`fee=-0.02430358`。
- `positions_rest_fallback=283`、`flat_dust_unhedged_cleared=1`。
- 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures XRPUSDT position=0.0`、`SPOT XRP available=0.000046`、`SPOT XRP frozen=0.0`。

### 推論
- 実 fill の parse は成功し、price / size / fee は 0 ではない。
- 安全復旧は成功。ただし初回 HEDGE が post-only のため、spot hedge ではなく futures flatten になり、収益化には不利。
- `use_spot_limit_ioc=true` の設定意図と実装がズレていた。

### 実装
- `bot/oms/oms.py`
  - `use_spot_limit_ioc=true` の場合、初回 HEDGE から aggressive limit IOC を使うよう修正。
  - `use_spot_limit_ioc=false` の場合だけ旧 post-only then IOC ladder を維持。
- `tests/test_hedge_ladder.py`
  - true 時の即 IOC test と false 時の旧 ladder test に分離。

### 検証
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ladder.py tests\test_hedge_ticket_flatten_race.py tests\test_fill_parser.py`: `31 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `106 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 修正後 live で HEDGE spot IOC が実際に約定するかは未確認。

---

## 2026-06-02 18:13 JST B案 latent replay / daily stopなし仮定

### 観測事実
- 対象ログ:
  - `runtime_logs\live_symbol_XRPUSDT_after_hedge_ioc_fix_15min_20260602_164429`
  - `runtime_logs\live_symbol_XRPUSDT_after_hedge_ioc_fix_30min_20260602_170049`
  - `runtime_logs\live_symbol_XRPUSDT_after_hedge_ioc_fix_60min_20260602_173149`
- 追加: `scripts\analyze_latent_replay.py`
  - `QUOTE_ASK` active中に `trade_side=buy` かつ `trade_px >= quote_price` を latent fill として抽出。
  - entry は futures ask quote、hedge は fill後5秒以降の `mid_spot` 優先proxy、cost は perp maker `1.4bps` + spot taker `10bps` + slippage `2bps`。
  - `latent_fills.csv`、`prefix_summary.csv`、`latent_replay.sqlite`、`RESULT_LATENT_REPLAY.md` を出力。
- 検証結果:
  - 15分: `quotes=376`、`latent_fills=0`、`active_buy_rows=81`、`min_active_ask_gap_bps=10.2475`
  - 30分: `quotes=698`、`latent_fills=0`、`active_buy_rows=247`、`min_active_ask_gap_bps=8.6881`
  - 60分途中: `quotes=649`、`latent_fills=0`、`active_buy_rows=710`、`min_active_ask_gap_bps=3.9560`
- `orders.jsonl` 集計でも実fillイベントは未検出。`pnl.jsonl` tail は `net_pnl=0.0`。
- `config.yaml` に daily stop / stop loss / drawdown limit 設定は未検出。

### 推論
- 現ログ定義では、今日の latent 17件は再現しない。別ログまたは別定義の可能性が高い。
- B案単体は10 trades未達のため判断保留。
- daily stopなし仮定の仮想結果は、latent fillが0件のため `net=0`、停止後latent損益も `0`。

### 検証
- `.venv\Scripts\python.exe -m py_compile .\scripts\analyze_latent_replay.py`: 成功。
- `.venv\Scripts\python.exe .\scripts\analyze_latent_replay.py <log_dir> --cuts 10,30,50`: 3ログで成功。

### 未確定点
- `latent 17件` の元定義・元ログは未特定。
- 60分ログは18:13時点で書き込み継続中のため途中集計。

---

## 2026-06-02 XRPUSDT HEDGE IOC 修正後 live 15分/30分/60分

### 観測事実
- 15分: `runtime_logs\live_symbol_XRPUSDT_after_hedge_ioc_fix_15min_20260602_164429`
  - `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`、実 fill `0`。
  - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures XRPUSDT position=0.0`、`SPOT XRP frozen=0.0`。
- 30分: `runtime_logs\live_symbol_XRPUSDT_after_hedge_ioc_fix_30min_20260602_170049`
  - `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`、実 fill `0`。
  - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures XRPUSDT position=0.0`、`SPOT XRP frozen=0.0`。
- 60分: `runtime_logs\live_symbol_XRPUSDT_after_hedge_ioc_fix_60min_20260602_173149`
  - `HALTED=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`、実 fill `0`。
  - `QUOTE_ASK post_only` 新規で `resp_code=45001` が `38` 件。Bitget response msg は `Unknown error`。
  - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures XRPUSDT position=0.0`、`SPOT XRP frozen=0.0`。

### 推論
- HEDGE IOC 修正後の bounded live は安全終了。残留・position drift は再発していない。
- 実 fill が出ていないため、HEDGE spot IOC の実約定パスは未確認。
- `45001` は `QUOTE_* post_only` の maker post race として扱うのが妥当。通常の risk reject / reject streak に積むと live 判定が汚れる。

### 実装
- `bot/oms/oms.py`
  - `QUOTE_BID` / `QUOTE_ASK` の `post_only` limit で `resp_code=45001` の場合、`post_only_quote_reject_skipped` としてログし、reject streak に積まない。
- `tests/test_hedge_ticket_flatten_race.py`
  - `post_only` quote `45001` が `order_reject` を出さず、`reject_streak=0` のままになる test を追加。

### 検証
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ticket_flatten_race.py`: `16 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `107 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 修正後 live で `post_only_quote_reject_skipped` になるかは未確認。
- HEDGE spot IOC の実約定パスは未確認。

---

## 2026-06-02 WLDUSDT 実約定パス確認と fee dust 修正

### 観測事実
- DOGEUSDT bounded live:
  - 15分 `runtime_logs\live_symbol_DOGEUSDT_after_xrp_funding_drop_15min_20260602_223109`
    - `HALTED=0`、`order_reject=0`、`fill=0`、`shutdown_cancel_all_done=1`。
    - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures position=0.0`、`SPOT DOGE available=0.000006`。
  - 30分 `runtime_logs\live_symbol_DOGEUSDT_after_xrp_funding_drop_30min_20260602_225140`
    - `HALTED=0`、`order_reject=0`、`fill=0`、`shutdown_cancel_all_done=1`。
    - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures position=0.0`。
- WLDUSDT:
  - DRY 15分 `runtime_logs\dry_symbol_WLDUSDT_profitability_candidate_15min_20260602_232634`
    - `HALTED=0`、`order_reject=0`、`constraints_loaded=15`、`order_new=533`、`shutdown_cancel_all_done=1`。
  - live 15分 `runtime_logs\live_symbol_WLDUSDT_profitability_candidate_15min_20260602_234233`
    - `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`。
    - 実 fill `26`: `QUOTE_ASK=4`、`HEDGE=7`、`FLATTEN futures=7`、`FLATTEN spot=8`。
    - `ticket_done=4`、`ticket_failed=0`。
    - `open_delta_without_hedge_ticket=8`。
    - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures position=0.0`、`SPOT WLD available=0.004`、`SPOT WLD frozen=0`。

### 推論
- WLD の `HEDGE` spot IOC は実約定した。
- ただし spot fee が base coin `WLD` で引かれ、例: `buy 144 WLD` 後に spot 実増加が約 `143.856 WLD` になる。
- `delta=-0.144 WLD` は約 `0.06 USDT` の dust だが、従来の `delta_tolerance=0.01 WLD` を超え、`open_delta_without_hedge_ticket` が誤発火して全量 FLATTEN した。
- PnL logger も base coin fee `0.144 WLD` を `0.144 USDT` として加算し、fee / net_pnl を過大悪化させていた。

### 実装
- `bot/config.py`
  - `StrategyConfig.delta_tolerance_notional` を追加。デフォルト `0.2 USDT`。`config.yaml` は変更なし。
- `bot/strategy/mm_funding.py`
  - `_open_delta_exceeds_tolerance()` を追加。
  - `open_delta_without_hedge_ticket` と HEDGING 幅拡大判定で、base数量だけでなく notional dust を許容。
- `bot/oms/oms.py`
  - spot fee が base coin の場合、PnL fee を `abs(fee) * price` で USDT 換算。
- `tests/test_hedge_ticket_flatten_race.py`
  - WLD fee dust 相当の delta が notional tolerance 内なら FLATTEN 対象外になる test を追加。
- `tests/test_pnl_logger.py`
  - base coin fee の USDT 換算 test を追加。

### 検証
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ticket_flatten_race.py tests\test_pnl_logger.py`: `19 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `109 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 修正後 live で WLD の HEDGE 後に inventory を維持し、即 FLATTEN しないことは未確認。
- funding 1bps 環境では edge が薄いため、実収益化は fill 後の保持/UNWIND パス再検証が必要。

---

## 2026-06-03 WLDUSDT fee dust 修正後 live 再確認と追加修正

### 観測事実
- 修正 commit `4c7bf52 fix: tolerate spot fee dust after hedges` を push 済み。
- WLDUSDT DRY 15分 `runtime_logs\dry_symbol_WLDUSDT_after_fee_dust_fix_15min_20260603_000509`
  - `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`order_new=503`、`order_cancel=503`。
  - `open_delta_without_hedge_ticket=0`、`shutdown_cancel_all_done=1`。
- WLDUSDT live 15分 `runtime_logs\live_symbol_WLDUSDT_after_fee_dust_fix_15min_20260603_002103`
  - `HALTED=0`、`order_reject=0`、`fill_parse_warning=0`、`shutdown_cancel_all_done=1`。
  - 実 fill `16`: `QUOTE_ASK=4`、`QUOTE_BID=1`、`HEDGE=6`、`FLATTEN futures=3`、`FLATTEN spot=2`。
  - `ticket_done=4`、`ticket_failed=1`。
  - `open_delta_without_hedge_ticket=841`、`spot_flatten_available_precheck=841`。
  - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures position=0.0`、`SPOT WLD available=0.858`、`SPOT WLD frozen=0`。

### 推論
- notional dust 許容だけでは不十分。
- `0.854 WLD` 程度の残留は約 `0.34 USDT` だが、spot constraints で `blocked_constraints` となり、BOT からは清算不能 dust。
- 清算不能 dust に対して `open_delta_without_hedge_ticket` が毎 cycle 発火し、FLATTEN skip が連発している。
- 起動時も `SPOT WLD available=0.858` は `delta_tolerance=0.01` を超えるため、最小取引数量未満 dust を考慮しないと live 再起動で誤 halt する。

### 実装
- `bot/strategy/mm_funding.py`
  - `_delta_below_min_trade()` を追加。
  - spot / futures constraints の `min_qty` / `min_notional` 未満の delta は `open_delta_without_hedge_ticket` 対象外にする。
- `bot/oms/oms.py`
  - `reconcile_startup_spot_balance()` で spot constraints の `min_qty` 未満残高を dust として扱い、live 起動時の誤 halt を避ける。
- `tests/test_hedge_ticket_flatten_race.py`
  - min trade 未満 delta が FLATTEN 対象外になる test を追加。
- `tests/test_startup_reconciliation.py`
  - min trade 未満 spot dust が startup halt にならない test を追加。

### 検証
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ticket_flatten_race.py tests\test_startup_reconciliation.py tests\test_pnl_logger.py`: `26 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `111 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 追加修正後 live で `open_delta_without_hedge_ticket` が抑制されるかは未確認。
- `ticket_failed=1` の初回 HEDGE pending expiry は別途確認対象。

---

## 2026-06-03 WLDUSDT untradeable dust / hedge fill race 追加修正

### 観測事実
- WLDUSDT live 15分 `runtime_logs\live_symbol_WLDUSDT_after_untradeable_dust_fix_15min_20260603_005939`
  - 起動直後に `startup_open_spot_balance_detected` で `HALTED`。
  - `SPOT WLD available=0.858` は `min_qty=0.01` 以上だが、`min_notional=1.0 USDT` 未満。
- WLDUSDT live 15分 `runtime_logs\live_symbol_WLDUSDT_after_startup_min_notional_dust_fix_15min_20260603_011848`
  - startup HALT は解消。
  - ただし HEDGE 後の fee dust / min-notional dust に対して一部 `open_delta_without_hedge_ticket` FLATTEN が発生。
- WLDUSDT live 15分 `runtime_logs\live_symbol_WLDUSDT_after_open_delta_dust_guard_15min_20260603_013959`
  - `open_delta_flatten_dust_skipped=4`、`flat_dust_unhedged_cleared=1`。
  - 一部 HEDGE 完了直後の fill/position 反映 race で `open_delta` FLATTEN が残存。
- WLDUSDT live 15分 `runtime_logs\live_symbol_WLDUSDT_after_hedge_done_defer_15min_20260603_015902`
  - `HALTED=0`、`startup_open_spot_balance_detected=0`、`fill_parse_warning=0`。
  - `order_new_non000=0`、`resp_codes=00000/43001`。`43001` は cancel のみ。
  - `resp_code 22002=0`。文字列検索の `22002` は timestamp 誤検出。
  - `open_delta_without_hedge_ticket` decision 4件は全て `open_delta_flatten_dust_skipped` で実 FLATTEN 注文なし。
  - `open_delta_deferred_after_hedge_done=43`、`flat_dust_unhedged_cleared=2`。
  - 終了後 read-only: `SPOT open orders=0`、`Futures open orders=0`、`Futures position=0.0`、`SPOT WLD available=1.58054`、`SPOT WLD frozen=0`。

### 推論
- WLD の残留は spot fee / min-notional 未満 dust。
- startup dust 判定は `min_qty` だけでは不十分で、`min_notional` と spot price が必要。
- HEDGE ticket done 直後は spot fill と positions sync の反映順がずれ、dust delta を全量 FLATTEN と誤認する race がある。
- `43001` は cancel 対象が約定済み/消滅済みの応答で、今回の reject streak には積まれていない。

### 実装
- `bot/exchange/bitget_gateway.py`
  - spot ticker の read-only 価格取得 `get_spot_last_price()` を追加。
- `bot/oms/oms.py`
  - startup spot dust 判定に `min_notional` + spot price を追加。
  - `open_delta_without_hedge_ticket` の OMS 側 dust precheck を追加し、dust FLATTEN を skip。
  - min-notional dust でも stale `unhedged_qty` を clear できるよう修正。
  - HEDGE ticket done 直後を検出する `recent_hedge_ticket_done()` を追加。
- `bot/strategy/mm_funding.py`
  - HEDGE done 直後の `open_delta` FLATTEN を短時間 defer し、quote cancel に留める。
  - flat dust clear に `delta_tolerance_notional` を渡す。
- `tests/test_startup_reconciliation.py`
  - `min_qty` 以上 / `min_notional` 未満の startup dust test を追加。
- `tests/test_hedge_ticket_flatten_race.py`
  - open-delta dust FLATTEN skip、min-notional dust unhedged clear、HEDGE done 直後 defer の tests を追加。

### 検証
- `.venv\Scripts\python.exe -m pytest -q`: `115 passed`。
- `git diff -- config.yaml`: 差分なし。
- live bounded 15分後 read-only: open orders 0 / futures position 0 / WLD dust のみ。

### 未確定点
- `ticket_failed=2` は残る。HEDGE IOC pending expiry / cancel 43001 の可観測性改善は次の確認対象。
- WLDUSDT の 15分 PnL は fee / hedge slip 優勢で、収益性はまだ未確定。

---

## 2026-06-03 P0/P1/P2 収益化確認と HEDGE ticket 可観測性修正

### 観測事実
- P0 DRY 15分: `runtime_logs\dry_symbol_WLDUSDT_wide22_p0_15min_20260603_120042`
  - `DRY_RUN=1`、`HALTED=0`、`order_reject=0`、`fill_parse_warning=0`。
  - `shutdown_cancel_all_done=2`、`shutdown_cancel_all_failed=0`。
  - `positions_monitor_heartbeat` / `fill_monitor_heartbeat` / `book_rx_rate` あり。
  - `positions_empty=true`。
  - `BASE_HALF_SPREAD_BPS=22` / `MIN_HALF_SPREAD_BPS=22` で `expected_edge_bps=16.2`。
  - `pre_quote_decision=2275`、`quote_any_rate=0.862`。
  - dry order は `QUOTE_ASK` のみ。`spot_hedge_sell_available_reduce=1444`。
- P1 live 15分ログ再分析: `runtime_logs\live_symbol_WLDUSDT_after_hedge_done_defer_15min_20260603_015902`
  - `ticket_failed=2` は `fail_reason=flatten_started`。
  - 1件目: `filled_qty=63.46 / want_qty=120.0`。
  - 2件目: `filled_qty=0.0 / want_qty=119.0`。
  - fill の `price / size / fee` は 0 なし。
- P2 Bitget public read-only scan:
  - WLDUSDT: 24h volume 約 `113M USDT`、funding `1.0bps`、futures spread 約 `2.54bps`、spot spread 約 `5.09bps`。
  - SKYAIUSDT / APRUSDT は funding が高いが spot spread が広く、即 live 候補としてはリスク大。
  - BTCUSDT / ETHUSDT は liquidity は強いが funding と volatility が低め。

### 推論
- 現行 WLDUSDT は spread を 22bps に広げると計算上 edge は出るが、既存 spot dust / available 制約により bid が片側化しやすい。
- `ticket_failed=2` は HEDGE failure というより、flatten/unwind 側の解消処理へ移った ticket の監視上の分類問題。
- 次の live で見るべき主指標は `ticket_superseded`、実 fill 後の `perp_pos + spot_pos ≒ 0`、片側 quote の継続時間。

### 実装
- `bot/config.py`
  - runtime env override 追加: `TARGET_NOTIONAL`、`BASE_HALF_SPREAD_BPS`、`MIN_HALF_SPREAD_BPS`、`QUOTE_REFRESH_MS`、`HEDGE_AGGRESSIVE_BPS`、`HEDGE_DEADLINE_SEC`。
- `bot/oms/oms.py`
  - `flatten_started` で open HEDGE ticket を `ticket_failed` ではなく `ticket_superseded` として記録。
  - `halt` 等の明示失敗は `ticket_failed` のまま維持。
- `tests/test_config_apis.py`
  - runtime env override test 追加。
- `tests/test_hedge_ladder.py`
  - `ticket_superseded` と `ticket_failed` の分離 test 追加。
- `tests/test_hedge_ticket_flatten_race.py`
  - flatten 開始時の期待値を `ticket_superseded` に更新。

### 検証
- `.venv\Scripts\python.exe -m pytest -q tests\test_config_apis.py`: `3 passed`。
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ladder.py tests\test_config_apis.py`: `7 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `118 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- DRY では実約定 PnL は検証不可。wide22 の live bounded 15分で fill path を再確認する必要あり。
- WLDUSDT は spot available 制約により bid が片側化するため、spot dust を手動でフラット化しない限り対称 quote の評価は不完全。

---

## 2026-06-03 read-only account state helper と live bounded 前確認

### 観測事実
- `bot.app` 実行中プロセスなし。
- `scripts\check_readonly_account_state.py` を追加。
  - authenticated GET endpoint のみ使用。
  - 注文、キャンセル、決済、設定変更なし。
- 初回実行で futures pending payload `{"entrustedList": null}` を wrapper dict と誤カウントし、`futures_open_orders=1` と表示。
- parser 修正後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=1.58054`
  - `SPOT WLD frozen=0.0`

### 推論
- 実口座残留は WLD spot dust のみ。
- WLD spot dust は約 `0.6 USDT` で、min-notional 未満のため通常注文では清算不能。
- wide22 live bounded 15分は実施可能だが、bid 側は spot available 制約で片側化しやすい。

### 実装
- `scripts/check_readonly_account_state.py`
  - spot/futures open orders、futures position、spot available/frozen を JSON 出力。
  - Bitget pending order wrapper の `entrustedList=null` を open orders 0 と扱う `_order_rows()` を追加。

### 検証
- `.venv\Scripts\python.exe -m py_compile scripts\check_readonly_account_state.py`: pass。
- `$env:SYMBOL='WLDUSDT'; .venv\Scripts\python.exe scripts\check_readonly_account_state.py --config config.yaml`: read-only 実行成功。

### 未確定点
- wide22 live bounded 15分で実 fill が発生するかは未確認。
- `ticket_superseded` が live fill path で想定通り出るかは未確認。

---

## 2026-06-03 WLDUSDT wide22 live bounded 15分と shutdown flatten 修正

### 観測事実
- commit `6b43578 chore: add read-only account state check` push 後に live bounded 15分を実行。
- 実行 env:
  - `SYMBOL=WLDUSDT`
  - `BASE_HALF_SPREAD_BPS=22`
  - `MIN_HALF_SPREAD_BPS=22`
  - `DRY_RUN=0`
  - `REAL_RUN_OK=1`
- live 15分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_after_superseded_15min_20260603_130039`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `resp_code 22002=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - `ticket_failed=0`
  - `ticket_superseded=2`
  - fills `18`
  - fill の `price / size / fee` は 0 なし。
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=-6.0`
  - `SPOT WLD available=7.58354`
  - `SPOT WLD frozen=0.0`
- 残留解消:
  - `scripts\flatten_account_state.py --execute` を `FLATTEN_ACCOUNT_OK=1` 付きで実行。
  - futures `buy 6 reduce_only market`
  - spot `sell 7.58354 limit_ioc`
  - 実行後 read-only:
    - `SPOT open orders=0`
    - `Futures open orders=0`
    - `Futures WLDUSDT position=0.0`
    - `SPOT WLD available=0.00354`
    - `SPOT WLD frozen=0.0`

### 推論
- live 15分中の HEDGE / FLATTEN 実約定 path は安全側に処理された。
- `ticket_superseded=2` は想定どおり `ticket_failed` から分離された。
- 終了後残留は、`spot + perp ≒ 0` のヘッジ在庫を通常戦略が許容し、shutdown が cancel only だったため。
- 常駐ではヘッジ在庫は戦略上あり得るが、bounded validation の終了条件は flat なので shutdown flatten が必要。

### 実装
- `scripts/flatten_account_state.py`
  - plan-only が既定。
  - 実注文には `--execute` と `FLATTEN_ACCOUNT_OK=1` を必須化。
  - futures reduce-only market と spot limit IOC で現在状態を flatten。
- `bot/app.py`
  - `SHUTDOWN_FLATTEN_POSITIONS=1` の時だけ shutdown 時に position flatten を実行。
  - `shutdown_flatten_positions_start/done/failed` を system log に出力。
- `scripts/run_bot_for_duration.py`
  - bounded runner の子プロセス env に `SHUTDOWN_FLATTEN_POSITIONS=1` を既定付与。
  - ユーザーが明示設定済みなら上書きしない。
- `tests/test_stop_bot_scripts.py`
  - bounded runner の shutdown flatten env と app log 経路を固定。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\app.py scripts\run_bot_for_duration.py scripts\flatten_account_state.py scripts\check_readonly_account_state.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_stop_bot_scripts.py tests\test_config_apis.py tests\test_hedge_ladder.py tests\test_hedge_ticket_flatten_race.py`: `33 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `120 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- shutdown flatten 追加後の live bounded 再確認は未実施。
- wide22 は fill 後も net PnL が改善したか、次回 shutdown flatten 付き live bounded 15分で再評価する。

---

## 2026-06-03 shutdown flatten 付き WLDUSDT live bounded 15分 再確認

### 観測事実
- commit `fd7fc9a fix: flatten positions on bounded shutdown` push 後に live bounded 15分を再実行。
- 実行 env:
  - `SYMBOL=WLDUSDT`
  - `BASE_HALF_SPREAD_BPS=22`
  - `MIN_HALF_SPREAD_BPS=22`
  - `DRY_RUN=0`
  - `REAL_RUN_OK=1`
- live 15分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_shutdown_flatten_15min_20260603_132329`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `resp_code 22002=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `ticket_failed=0`
  - `ticket_superseded=0`
  - fills `0`
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.00354`
  - `SPOT WLD frozen=0.0`

### 推論
- bounded runner の shutdown flatten は発火し、残留 position を作らず終了した。
- 今回は約定なしのため、fill 後の shutdown flatten 再確認はまだ未完了。
- WLD spot dust が `0.00354` まで低下したため、bid 側の spot available block は継続するが、残留リスクは dust のみ。

### 検証
- 終了後 read-only で open orders 0 / futures position 0 / spot frozen 0 を確認。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 実 fill 発生後に `shutdown_flatten_positions` が spot/perp 両脚を完全 flat に戻すかは次回 bounded で継続監視。
- wide22 の収益性は今回 fills 0 のため評価不可。

---

## 2026-06-03 WLDUSDT wide22 live bounded 30分と 22002 stale store 修正

### 観測事実
- shutdown flatten 付き live bounded 30分を実行。
- live 30分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_shutdown_flatten_30min_20260603_230844`
  - `HALTED=0`
  - `fill_parse_warning=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - fills `112`
  - fill の `price / size / fee` は 0 なし。
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.01339`
  - `SPOT WLD frozen=0.0`
- shutdown flatten 2回目で futures reduce-only close が `22002 No position to close` を返した。
- 実口座 futures は flat だったが、stale な positions store が `perp_pos=-115` を返したため `order_reject=1` / `reject_streak=1` に積まれた。

### 推論
- fill 後の bounded shutdown flatten は最終 read-only 上 flat に戻せた。
- `22002` は futures 側が既に flat の正常系だった。
- reject 計上の原因は、`22002` 直後の確認が REST 実ポジションではなく stale な WS positions store に引き戻されたこと。

### 実装
- `bot/oms/oms.py`
  - FLATTEN/UNWIND の futures reduce-only `22002` 直後は REST `get_perp_position()` を優先して同期。
  - REST が `0` を返す場合は `reduce_only_no_position_sync_flat` として `reject_streak` を積まない。
  - REST 同期結果を `rest_sync_used` でログ出力。
- `tests/test_hedge_ticket_flatten_race.py`
  - stale positions store が `-115`、REST が `0` を返す再現テストを追加。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\oms\oms.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ticket_flatten_race.py -q`: pass。
- `.venv\Scripts\python.exe -m pytest -q`: `121 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 修正後に同じ shutdown 22002 が live bounded で再発した場合、`order_reject=0` になるかは次回 live bounded で確認する。
- wide22 の収益性は net PnL がまだ弱く、継続評価が必要。

---

## 2026-06-03 22002 REST sync 修正後 live bounded 15分

### 観測事実
- commit `2b3a61e fix: use rest position sync for reduce-only 22002` push 後に live bounded 15分を実行。
- 実行前 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.01339`
  - `SPOT WLD frozen=0.0`
- live 15分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_after_22002_rest_sync_15min_20260603_234320`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `resp_code 22002` は出たが `reduce_only_no_position_rest_sync=1` / `reduce_only_no_position_sync_flat=1`
  - `reduce_only_no_position_sync_not_flat=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - fills `9`
  - fill の `price / size / fee` は 0 なし。
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.89939`
  - `SPOT WLD frozen=0.0`
- bot.app process remaining なし。

### 推論
- stale positions store が残る shutdown 22002 でも、REST position sync により reject streak を積まないことを live で確認した。
- bounded shutdown flatten は fill 後も futures flat / open orders 0 で終了できた。
- `SPOT WLD available=0.89939` は最小発注単位未満の dust と判断する。

### 検証
- read-only で open orders 0 / futures position 0 / spot frozen 0 を確認。
- ログ集計で `order_reject=0`、`reduce_only_no_position_sync_not_flat=0` を確認。

### 未確定点
- wide22 の収益性は、15分 run の last net PnL が `-0.000051964999999999995` でまだ優位性未確定。
- PnL ログは fill ベースの手数料・スプレッド収益を十分反映していない可能性があり、収益判断は read-only 残高差分または約定再構成が必要。

---

## 2026-06-03 side-edge guard 実装

### 観測事実
- WLDUSDT wide22 live fill を QUOTE/HEDGE で概算突合。
- `runtime_logs\live_symbol_WLDUSDT_wide22_shutdown_flatten_30min_20260603_230844`
  - matched quote/hedge qty `1774.15`
  - gross `-0.973455 USDT`
  - known fees `1.0984679834 USDT`
  - rough net `-2.0719229834 USDT`
- `runtime_logs\live_symbol_WLDUSDT_wide22_after_22002_rest_sync_15min_20260603_234320`
  - matched quote/hedge qty `227.0`
  - gross `-0.05707699999999394 USDT`
  - known fees `0.153128942 USDT`
  - rough net `-0.21020594199999393 USDT`

### 推論
- wide22 は安全性は改善したが、spot hedge 実行価格を含む片側 edge が負で、約定ごとに損失になっている。
- 既存の total edge 判定は futures quote spread と概算コストだけを見ており、`perp quote price` と `spot hedge executable price` の basis/IOC cost を片側ごとに見ていない。

### 実装
- `bot/config.py`
  - `StrategyConfig.side_edge_guard_enabled` / `side_edge_min_bps` を追加。
  - env `SIDE_EDGE_GUARD` / `SIDE_EDGE_MIN_BPS` で上書き可能にした。
- `bot/strategy/mm_funding.py`
  - bid quote は `spot bid` で売り hedge した場合の edge を計算。
  - ask quote は `spot ask` で買い hedge した場合の edge を計算。
  - `fee_maker_perp_bps + fee_taker_spot_bps + slippage_bps + adverse_buffer_bps` を片側 cost として控除。
  - guard 有効時に side edge が閾値未満なら、その片側 quote の size を `0` にして `side_edge_guard_block` を出す。
- `tests/test_total_edge.py`
  - 負の spot hedge edge を bid/ask とも block するケースを追加。
  - favorable ask のみ通すケースを追加。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\config.py bot\strategy\mm_funding.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_total_edge.py`: `3 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `123 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- `SIDE_EDGE_GUARD=1` の live bounded で fill 数と rough net が改善するかは未確認。
- guard が厳しすぎて約定ゼロになる可能性があるため、まず 15分 bounded で通過率と block 率を見る。

---

## 2026-06-04 side-edge guard live bounded 15分と shutdown spot 残留修正

### 観測事実
- commit `850dbe5 feat: add side edge guard for spot hedge cost` push 後に `SIDE_EDGE_GUARD=1` で live bounded 15分を実行。
- live 15分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_side_edge_guard_15min_20260604_000546`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - fills `2`
  - fill の `price / size / fee` は 0 なし。
  - `side_edge_guard_block=1954`
  - pre-quote pass/block:
    - bid pass `787` / block `1438`
    - ask pass `1591` / block `634`
  - side-edge 分布:
    - bid P50 `-1.1856bps`, P90 `1.5585bps`
    - ask P50 `0.8236bps`, P90 `3.0631bps`
- 終了後 read-only で `SPOT WLD available=102.09539` が残留。futures position / open orders は 0。
- `scripts\flatten_account_state.py --execute` を `FLATTEN_ACCOUNT_OK=1` 付きで実行し、spot sell `102.09539` を実行。
- 手動 flatten 後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.00539`
  - `SPOT WLD frozen=0.0`

### 推論
- side-edge guard は負 edge の片側 quote を抑制し、fill 数を大きく減らした。
- ただし ask は一部通り、実 fill 後に shutdown spot flatten が部分約定で終わった。
- shutdown flatten は spot IOC 注文を出したが、約定確認前に `shutdown_flatten_positions_done` を出したため、実口座 spot long が残った。

### 実装
- `bot/oms/oms.py`
  - `shutdown_flatten_positions` の spot flatten 価格を `hedge_aggressive_bps` 分だけ aggressive にした。
- `bot/app.py`
  - shutdown flatten 前後に REST read-only で spot available / futures position を確認。
  - REST 値を OMS internal position に同期してから flatten する。
  - fresh BBO で最大 `SHUTDOWN_FLATTEN_MAX_ATTEMPTS` 回 retry。
  - 残留が `delta_tolerance_notional` を超える場合は `shutdown_flatten_positions_failed` / `shutdown_flatten_positions_residual` を出し、done 扱いしない。
  - `shutdown_flatten_positions_check` を追加。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\app.py bot\oms\oms.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_stop_bot_scripts.py tests\test_hedge_ticket_flatten_race.py tests\test_total_edge.py`: `30 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `123 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 修正後の shutdown spot 残留解消は live bounded で未確認。
- side-edge guard は約定を減らすが、収益性改善はまだ未確認。

---

## 2026-06-04 shutdown residual 修正後 side-edge guard live bounded 15分

### 観測事実
- commit `9d4c39e fix: verify shutdown flatten residual positions` push 後に `SIDE_EDGE_GUARD=1` で live bounded 15分を再実行。
- live 15分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_side_edge_guard_shutdown_verify_15min_20260604_002452`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_check=4`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `shutdown_flatten_positions_residual=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - fills `0`
  - `side_edge_guard_block=3200`
- shutdown check:
  - `spot_available=0.00539`
  - `perp_position=0.0`
  - `spot_notional=0.002851849`
  - `flat=true`
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.00539`
  - `SPOT WLD frozen=0.0`
- bot.app process remaining なし。

### 推論
- shutdown residual 確認修正は、少なくとも no-fill run では正常に flat 判定して終了した。
- side-edge guard は現在の WLDUSDT 条件では quote をかなり抑制し、15分では約定なし。
- 収益化には「guard を通る favorable basis の時間帯を待つ」か「銘柄/閾値/幅の再探索」が必要。

### 検証
- read-only で open orders 0 / futures position 0 / spot frozen 0 を確認。
- ログで `shutdown_flatten_positions_failed=0` / `shutdown_flatten_positions_residual=0` を確認。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- side-edge guard 有効時に実 fill が発生した場合の rough net は未確認。
- 15分では fills 0 のため、収益性改善の判断は未完了。

---

## 2026-06-04 side-edge guard live bounded 30分と最小注文額未満 dust 判定修正

### 観測事実
- `SIDE_EDGE_GUARD=1` で WLDUSDT wide22 live bounded 30分を実行。
- live 30分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_side_edge_guard_30min_20260604_020454`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_done=0`
  - `shutdown_flatten_positions_failed=1`
  - `shutdown_flatten_positions_residual=1`
  - fills `13`
  - fill の `price / size / fee` は 0 なし。
  - `side_edge_guard_block=6473`
- rough QUOTE/HEDGE 突合:
  - matched qty `245.35`
  - gross `0.9371629999999852 USDT`
  - known fees `0.14399728312280702 USDT`
  - rough net `0.7931657168771782 USDT`
  - sell quote side net `0.8313142868771896 USDT`
  - buy quote side net `-0.0381485700000114 USDT`
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.89504`
  - `SPOT WLD frozen=0.0`
- `scripts\flatten_account_state.py --execute` で spot sell `0.89504` を試行したが、Bitget が `45110 less than the minimum amount 1 USDT` を返した。
- read-only は open orders 0 / futures position 0 のまま。

### 推論
- side-edge guard 有効時の 30分 run では、実 fill 後の rough net がプラスに転じた。
- 残留 `0.89504 WLD` は notional 約 `0.466 USDT` で、Bitget spot 最小注文額 `1 USDT` 未満のため決済不能 dust。
- shutdown residual 判定が `delta_tolerance_notional=0.2` のみを見ており、取引所の `min_notional` を考慮していなかった。

### 実装
- `bot/app.py`
  - `_shutdown_position_snapshot` で spot constraints の `min_notional` を取得。
  - spot notional の flat 判定閾値を `max(delta_tolerance_notional, spot.min_notional)` に変更。
  - `spot_flat_notional_threshold` を shutdown check log に出力。
- `tests/test_stop_bot_scripts.py`
  - `spot_notional < spot.min_notional` の場合に shutdown snapshot が flat と判定するテストを追加。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\app.py tests\test_stop_bot_scripts.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_stop_bot_scripts.py`: `6 passed`。
- `.venv\Scripts\python.exe -m pytest -q`: `124 passed`。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- 最小注文額未満 dust を flat 扱いする修正後の live shutdown check は未確認。
- side-edge guard の rough net プラスは 30分1本の結果で、継続性は未確認。

---

## 2026-06-04 最小注文額未満 dust flat 判定 live bounded 5分確認

### 観測事実
- commit `5df4b7b fix: treat below-minimum spot dust as flat on shutdown` push 後に `SIDE_EDGE_GUARD=1` で live bounded 5分を実行。
- live 5分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_dust_flat_threshold_5min_20260604_023908`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `startup_open_spot_balance_detected=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_check=4`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `shutdown_flatten_positions_residual=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - fills `0`
- shutdown check:
  - `spot_available=0.89504`
  - `perp_position=0.0`
  - `spot_notional=0.46747939199999994`
  - `spot_flat_notional_threshold=1.0`
  - `flat=true`
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.89504`
  - `SPOT WLD frozen=0.0`
- bot.app process remaining なし。

### 推論
- Bitget spot 最小注文額未満 dust を shutdown failure 扱いしない修正は live で確認できた。
- `0.89504 WLD` は売却不能 dust として残るが、open orders / futures position は 0。

### 検証
- read-only で open orders 0 / futures position 0 / spot frozen 0 を確認。
- ログで `shutdown_flatten_positions_done=1` / failed 0 / residual 0 を確認。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- side-edge guard の rough net プラス継続性は未確認。
- 次は 60分 bounded で rough net と shutdown flat を再評価する。

---

## 2026-06-04 WLDUSDT side-edge guard live bounded 60分確認

### 観測事実
- commit `91d81ff docs: record below-minimum dust live verification` 後に `SIDE_EDGE_GUARD=1` で WLDUSDT wide22 live bounded 60分を実行。
- live 60分 log: `runtime_logs\live_symbol_WLDUSDT_wide22_side_edge_guard_60min_20260604_024521`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `startup_open_spot_balance_detected=0`
  - `shutdown_flatten_positions_start=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `shutdown_flatten_positions_residual=0`
  - `shutdown_cancel_all_done=2`
  - `shutdown_cancel_all_failed=0`
  - `ticket_failed=0`
  - `ticket_superseded=0`
  - fills `0`
- shutdown check:
  - `spot_available=0.89504`
  - `perp_position=0.0`
  - `spot_notional=0.4597372959999999`
  - `spot_flat_notional_threshold=1.0`
  - `flat=true`
- order response:
  - `order_new=285`
  - `order_cancel=285`
  - `resp_code 00000=570`
  - `resp_code 22002` は注文応答には出ていない。
- pre_quote:
  - rows `9070`
  - final block: `side_edge_guard_block=5774`, `quote_fade=903`, `spot_hedge_sell_available_block=366`, `none=2027`
  - `final_should_quote_bid`: true `2027`, false `7043`
  - `final_should_quote_ask`: true `2393`, false `6677`
  - `spot_hedge_sell_available_block=True` は `6140`
  - `bid_side_edge_bps`: p10 `0.23274717731281136`, p50 `2.500222346717985`, p90 `4.887325097538424`
  - `ask_side_edge_bps`: p10 `-5.161487387648146`, p50 `-2.6106790790411605`, p90 `-0.44717720220331536`
- 終了後 read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures WLDUSDT position=0.0`
  - `SPOT WLD available=0.89504`
  - `SPOT WLD frozen=0.0`
- bot.app process remaining なし。
- `git diff -- config.yaml`: 差分なし。

### 推論
- safety 側は 60分でも合格。最小注文額未満 dust を flat 扱いする shutdown 修正は継続確認できた。
- 収益化側は fills 0 のため未確定。
- WLDUSDT wide22 + side-edge guard は、ask side の edge が弱く、bid side は `spot_hedge_sell_available_block` が大きい。dust のみ状態では片側機会を取り切れない。

### 検証
- `.venv\Scripts\python.exe scripts\analyze_live_profitability.py runtime_logs\live_symbol_WLDUSDT_wide22_side_edge_guard_60min_20260604_024521` で集計。
- read-only で open orders 0 / futures position 0 / spot frozen 0 を確認。

### 未確定点
- side-edge guard 有効時の実 fill 継続収益性は未確認。
- P2 は、注文せずに銘柄別の side-edge 通過率と quote 可能性を public/read-only で比較する。

---

## 2026-06-04 P2 public/read-only side-edge 銘柄scan追加

### 観測事実
- 注文・キャンセル・決済・live起動なしで、Bitget public ticker だけを読む scanner を追加。
- 変更ファイル:
  - `scripts\scan_side_edge_symbols.py`
  - `tests\test_scan_side_edge_symbols.py`
  - `STATUS.md`
- scanner の計算式は strategy の `_quote_side_edge_fields` と同じ:
  - bid side: `(spot_bid_hedge - perp_quote_bid) / mid_spot * 10000 - side_cost_bps`
  - ask side: `(perp_quote_ask - spot_ask_hedge) / mid_spot * 10000 - side_cost_bps`
- `BASE_HALF_SPREAD_BPS=22`, `MIN_HALF_SPREAD_BPS=22`, `SIDE_EDGE_MIN_BPS=0`, `--min-perp-quote-volume 10000000`, `--max-abs-mid-basis-bps 200` で public scan を実行。
- 上位候補:
  - `SKYAIUSDT`: best `29.1009bps`, ask pass, mid_basis `29.5474bps`
  - `BCHUSDT`: best `17.1405bps`, bid pass, mid_basis `-17.5754bps`
  - `MYXUSDT`: best `16.1917bps`, bid pass, mid_basis `-20.1207bps`
  - `DOTUSDT`: best `15.0458bps`, bid pass, mid_basis `-17.9775bps`
  - `INJUSDT`: best `14.3795bps`, bid pass, mid_basis `-15.8276bps`
  - `FILUSDT`: best `13.8046bps`, bid pass, mid_basis `-17.5625bps`
  - `MAGMAUSDT`: best `13.6696bps`, ask pass, mid_basis `12.7172bps`
  - `RENDERUSDT`: best `13.1795bps`, bid pass, mid_basis `-13.9308bps`
  - `ENAUSDT`: best `10.1630bps`, bid pass, mid_basis `-17.6471bps`
  - `ONDOUSDT`: best `9.8687bps`, bid pass, mid_basis `-9.4731bps`
- 主要候補:
  - `SOLUSDT`: best `7.2678bps`, bid pass, ask fail
  - `ETHUSDT`: best `7.1806bps`, bid pass, ask fail
  - `XRPUSDT`: best `6.9248bps`, bid pass, ask fail
  - `SUIUSDT`: best `5.8246bps`, bid pass, ask fail
- 直近WLDUSDT 60分runと同じく、wide22条件では多くの銘柄が bid side 優位 / ask side 劣位。

### 推論
- 現状の `spot_available=dust` では bid quote 後の spot sell hedge が在庫不足で詰まりやすい。これは `spot_hedge_sell_available_block=True 6140` と整合。
- 収益化の次候補は「ask side もpassする銘柄」か「spot在庫制約を明示管理したうえでbid sideも使う設計」。
- `SKYAIUSDT` / `MAGMAUSDT` は ask side がpassするが、銘柄リスク・板厚・約定品質は未確認。
- `SOL/ETH/XRP/SUI` は流動性は高いが、このsnapshotではask sideが弱く、spot在庫なし運用には不利。

### 検証
- `.venv\Scripts\python.exe -m py_compile scripts\scan_side_edge_symbols.py tests\test_scan_side_edge_symbols.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_scan_side_edge_symbols.py`: `2 passed`。
- public scan は REST read-only。注文系 endpoint は未使用。
- `git diff -- config.yaml`: 差分なし。

### 未確定点
- scanner は ticker snapshot の近似。WS板・quote_fade・cancel aggressive・実fill品質は未評価。
- ask-pass銘柄の実運用可否は dry bounded から確認が必要。

---

## 2026-06-04 SKYAIUSDT ask-pass 候補 dry bounded 15分確認

### 観測事実
- commit `57a3e13 feat: add read-only side edge symbol scanner` push 後、`SKYAIUSDT` を DRY bounded 15分で確認。
- dry 15分 log: `runtime_logs\dry_symbol_SKYAIUSDT_wide22_side_edge_guard_15min_20260604_035441`
- 実行条件:
  - `DRY_RUN=1`
  - `BOT_MODE=dry`
  - `SYMBOL=SKYAIUSDT`
  - `BASE_HALF_SPREAD_BPS=22`
  - `MIN_HALF_SPREAD_BPS=22`
  - `SIDE_EDGE_GUARD=1`
  - `SIDE_EDGE_MIN_BPS=0`
- 集計:
  - `HALTED=0`
  - `fill_count=0`
  - `order_new=535`
  - `order_cancel=535`
  - dry のため `order_resp_codes=None`
  - `shutdown_cancel_all_done=1`
  - `book_rx_rate=14`
  - `fill_monitor_heartbeat=15`
  - `positions_monitor_heartbeat=15`
  - `runtime_heartbeat=14`
- pre_quote:
  - rows `1885`
  - final block: `spot_hedge_sell_available_block=1258`, `quote_fade=502`, `none=125`
  - order intents: `QUOTE_ASK=1070`
  - `bid_side_edge_bps`: p10 `-30.151642662635055`, p50 `-27.14977467359183`, p90 `-23.911185336953977`
  - `ask_side_edge_bps`: p10 `23.787448449992958`, p50 `27.52941066127228`, p90 `30.525691665208143`
  - `final_should_quote_bid`: true `125`, false `1760`
  - `final_should_quote_ask`: true `1383`, false `502`
- `git diff -- config.yaml`: 差分なし。

### 推論
- scanner で ask-pass と出た `SKYAIUSDT` は、DRY実行中のWS/strategyログでも ask side edge が強く、方向性は一致した。
- ただし fills 0 のため、実約定品質と収益性は未確定。
- `spot_hedge_sell_available_block` は引き続き大きいが、実際の注文意図は `QUOTE_ASK` に偏っており、spot在庫dust運用でもWLDより検証価値がある。

### 検証
- `.venv\Scripts\python.exe scripts\analyze_live_profitability.py runtime_logs\dry_symbol_SKYAIUSDT_wide22_side_edge_guard_15min_20260604_035441` で集計。
- 追加の手動集計で side-edge 分布、heartbeat、intent を確認。

### 未確定点
- DRY 15分では fills 0。次は `SKYAIUSDT` の dry 60分または少額live boundedの前に、板厚/最小注文/制約を確認する。

---

## 2026-06-04 SKYAIUSDT P0/P1/P2 dry比較

### 観測事実
- `SKYAIUSDT` の制約を Bitget public/read-only で確認。
  - spot `min_qty=0.01`, `qty_step=0.01`, `min_notional=1.0`, `tick_size=0.00001`
  - perp `min_qty=1.0`, `qty_step=1.0`, `min_notional=5.0`, `tick_size=0.00001`
- public scan は引き続き `SKYAIUSDT` ask-pass。
  - `BASE_HALF_SPREAD_BPS=22`
  - `ask_side_edge_bps=22.27599291089232`
  - `bid_side_edge_bps=-21.88420930299933`
  - `mid_basis_bps=22.08082744995447`
- `SKYAIUSDT` wide22 DRY bounded 60分を実行。
  - log: `runtime_logs\dry_symbol_SKYAIUSDT_wide22_side_edge_guard_60min_20260604_170401`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_cancel_all_failed=0`
  - `book_rx_rate=59`
  - `fill_monitor_heartbeat=60`
  - `positions_monitor_heartbeat=58`
  - `order_new=2709`
  - `order_cancel=2709`
  - `QUOTE_ASK=5418`
  - fills `0`
  - `ask_side_edge_bps`: p10 `3.6333426215827043`, p50 `10.513847610086868`, p90 `17.56728623056423`
  - ask quote distance from micro: p10 `20.17043613505478`, p50 `21.696268356213828`, p90 `22.479355523754197`
  - quote lifetime: p10 `0.49535679817199707`, p50 `0.5130319595336914`, p90 `1.5149390697479248`
  - cancel reasons: `quote=1367`, `quote_fade=1340`, `cancel_aggressive=1`, `shutdown_cancel_all=1`
- `SKYAIUSDT` wide18 DRY bounded 15分を実行。
  - log: `runtime_logs\dry_symbol_SKYAIUSDT_wide18_side_edge_guard_15min_20260604_180532`
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_cancel_all_failed=0`
  - `book_rx_rate=14`
  - `fill_monitor_heartbeat=15`
  - `positions_monitor_heartbeat=15`
  - `order_new=696`
  - `order_cancel=696`
  - fills `0`
  - `ask_side_edge_bps`: p10 `7.618104812150092`, p50 `12.721665185547733`, p90 `19.656667976006737`
  - ask quote distance from micro: p10 `15.96187220714424`, p50 `16.99366106512176`, p90 `18.20453456071926`
  - quote lifetime: p10 `0.4958460330963135`, p50 `0.5178606510162354`, p90 `1.5307350158691406`
  - cancel reasons: `quote=380`, `quote_fade=315`, `shutdown_cancel_all=1`
- `git diff -- config.yaml`: 差分なし。

### 推論
- `SKYAIUSDT` は制約上、現行 `target_notional=50` でperp最小注文を満たす。
- wide22はquoteがmicroから約 `21.7bps` 外側で、DRYの観測上は約定期待が低い。
- wide18はquote距離がmicro p50 `16.99bps` まで近づき、ask edge p50も `12.72bps` 残るため、wide22より次の検証候補として強い。
- DRYでは実fillが発生しないため、収益性はlive boundedでしか確定できない。

### 検証
- Bitget public RESTのみ使用。注文・キャンセル・決済・live起動なし。
- `.venv\Scripts\python.exe scripts\analyze_live_profitability.py` と手動集計で確認。
- `config.yaml` 差分なし。

### 未確定点
- wide18の実fill頻度、post-only reject有無、HEDGE/UNWIND/FLATTEN実約定パス。
- 次に進めるなら `SKYAIUSDT` wide18 の live bounded 15分が最小の実約定確認。

---

## 2026-06-04 SKYAIUSDT wide18 live bounded 15分とmax position quote cap修正

### 観測事実
- 起動前read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SKYAIUSDT position=0.0`
  - `SPOT SKYAI available=0.0`
  - `SPOT SKYAI frozen=0.0`
- `SKYAIUSDT` wide18 live bounded 15分を実行。
  - log: `runtime_logs\live_symbol_SKYAIUSDT_wide18_side_edge_guard_15min_20260604_223341`
  - `DRY_RUN=0`
  - `SYMBOL=SKYAIUSDT`
  - `BASE_HALF_SPREAD_BPS=18`
  - `MIN_HALF_SPREAD_BPS=18`
  - `SIDE_EDGE_GUARD=1`
  - `SIDE_EDGE_MIN_BPS=0`
- safety:
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `startup_open_spot_balance_detected=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_cancel_all_failed=0`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `book_rx_rate=14`
  - `fill_monitor_heartbeat=15`
  - `positions_monitor_heartbeat=15`
- order / fill:
  - `order_new=628`
  - `order_cancel=616`
  - `resp_code 00000=1238`
  - `resp_code 43001=6`
  - text `22002=3`
  - fills `17`
  - fill の `price / size / fee` 0 件数はすべて `0`
  - intents: `QUOTE_ASK=1230`, `FLATTEN=8`, `HEDGE=6`
- rough QUOTE/HEDGE:
  - `QUOTE_ASK` fills `5`, qty `1524`
  - `SPOT:HEDGE` fills `4`, qty `1199`
  - rough `QUOTE_ASK/HEDGE` net known `0.33141224999999797 USDT`
- 実fill集計:
  - futures quote sell notional `279.98562`, fee `0.03919797 USDT`
  - spot hedge buy notional `219.55042`, fee概算 `0.21955042 USDT`
  - futures flatten buy notional `280.3229`, fee `0.11773561 USDT`
  - spot flatten sell notional `219.6888662`, fee `0.21968886 USDT`
  - spot手数料とFLATTEN込みでは手数料/flattenコストが粗利を上回る。
- pnl:
  - `pnl_net_sum=-0.6871733734999865`
  - `pnl_nonzero_rows=6`
- 終了後read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SKYAIUSDT position=0.0`
  - `SPOT SKYAI available=0.001`
  - `SPOT SKYAI frozen=0.0`
  - `spot_notional=0.000183865`, `spot_flat_notional_threshold=1.0`, `flat=true`
- ログ上、`max_position` が発生し、複数回の `FLATTEN` が走った。
- 原因箇所:
  - `bot\strategy\mm_funding.py` は `abs(spot_pos) * mid_spot > max_position_notional` または `abs(perp_pos) * mid_perp > max_position_notional` になってから `flatten(reason="max_position")` していた。
  - `target_notional=50` でも連続約定で片脚 `~100 USDT` を超え、運転中FLATTENが発生し得る。

### 推論
- safetyは合格。実fillパスも `QUOTE_ASK -> SPOT HEDGE -> FLATTEN` まで通った。
- 収益性は不合格。短時間boundedでFLATTENが入ると、spot taker feeとflatten feeが粗利を消す。
- 主因はedge不足ではなく、max positionを超えた後にFLATTENする設計。quote前に約定後片脚上限を超えるサイズを抑制すべき。

### 実装
- `bot\strategy\mm_funding.py`
  - quote前に `max_position_notional` から許容残qtyを計算し、`size_bid` / `size_ask` を制約丸め後に削減。
  - capで0になる場合は quote を抑制。
  - `max_position_quote_reduce` / `max_position_quote_block` をdecision logに追加。
  - `pre_quote_decision` に `max_position_quote_reduced`, `max_position_quote_block`, `size_bid_before_max_position_cap`, `size_ask_before_max_position_cap` を追加。
- `tests\test_phase_d_strategy.py`
  - max position到達前にquote sizeを削減し、`flatten` を呼ばないテストを追加。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\strategy\mm_funding.py tests\test_phase_d_strategy.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_phase_d_strategy.py`: `5 passed`。
- `config.yaml` 差分なし。

### 未確定点
- 修正後の `SKYAIUSDT wide18` live bounded再確認は未実施。
- bounded短時間ではshutdown flattenが入るため、収益性評価は継続運用と分けて見る必要がある。

---

## 2026-06-05 SKYAIUSDT wide18 cap修正後 live 15分再確認と1% buffer追加

### 観測事実
- commit `e05352f fix: cap quote size before max position flatten` 後に `SKYAIUSDT` wide18 live bounded 15分を実行。
- 起動前read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SKYAIUSDT position=0.0`
  - `SPOT SKYAI available=0.001`
  - `SPOT SKYAI frozen=0.0`
- live 15分 log: `runtime_logs\live_symbol_SKYAIUSDT_wide18_cap_fix_15min_20260605_001258`
- safety:
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `startup_open_spot_balance_detected=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_cancel_all_failed=0`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `book_rx_rate=14`
  - `fill_monitor_heartbeat=15`
  - `positions_monitor_heartbeat=15`
- order / fill:
  - `order_new=678`
  - `order_cancel=671`
  - `resp_code 00000=1346`
  - `resp_code 43001=3`
  - text `22002=5`
  - fills `10`
  - fill の `price / size / fee` 0 件数はすべて `0`
  - `max_position_quote_reduce=2074`
  - `max_position_quote_block=0`
  - `max_position=3`
- rough:
  - `QUOTE_ASK/HEDGE` rough known net `0.4471750799999987 USDT`
  - `pnl_net_sum=-2.180671950834993`
- 終了後read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SKYAIUSDT position=0.0`
  - `SPOT SKYAI available=0.005`
  - `SPOT SKYAI frozen=0.0`
- fill集計:
  - futures quote sell notional `160.03518`, fee `0.02240492 USDT`
  - spot hedge buy notional `159.5656`, fee概算 `0.1595656 USDT`
  - futures flatten buy notional `100.1545`, fee `0.04206488 USDT`
  - spot flatten sell notional `99.663409`, fee `0.09966341 USDT`
- capは発火したが、上限ぴったりまで許容したため、価格変動とspot base fee込みで `max_position` に再到達した。

### 推論
- 前回修正は方向性は正しいが、上限に余白がなく不十分。
- `max_position_notional=100` に対して quote cap は少なくとも spot taker fee / slippage / 短期価格変動ぶんの余白が必要。
- 短時間boundedではshutdown flattenの影響もあるが、運転中 `max_position` が残る限り次のlive延長は不可。

### 実装
- `bot\strategy\mm_funding.py`
  - quote cap に `effective_max_position_quote_notional` を導入。
  - `max_position_notional` の1%をbufferとして予約し、quote前capは `max_position_notional * 0.99` を上限にする。
  - bufferは `max(100bps, spot taker fee + slippage + adverse_buffer)`。
  - `max_position_quote_reduce/block` log に `effective_max_position_quote_notional` を追加。
- `tests\test_phase_d_strategy.py`
  - max position quote cap が実上限より小さいeffective上限を使うことを確認。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\strategy\mm_funding.py tests\test_phase_d_strategy.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_phase_d_strategy.py`: `5 passed`。

### 未確定点
- 1% buffer後の live bounded 再確認は未実施。
- 次の確認条件は `max_position=0`, `max_position_quote_reduce` 出力あり, 運転中FLATTENなし。

---

## 2026-06-05 SKYAIUSDT wide18 1% buffer後 live 15分確認とshutdown fill drain修正

### 観測事実
- commit `114f2f1 fix: reserve buffer in max position quote cap` 後に `SKYAIUSDT` wide18 live bounded 15分を実行。
- live 15分 log: `runtime_logs\live_symbol_SKYAIUSDT_wide18_cap_buffer_15min_20260605_003050`
- 起動前read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SKYAIUSDT position=0.0`
  - `SPOT SKYAI available=0.005`
  - `SPOT SKYAI frozen=0.0`
- safety:
  - `HALTED=0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `resp_code 22002=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_cancel_all_failed=0`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
- order / fill:
  - `order_new=259`
  - `order_cancel=255`
  - `resp_code 00000=512`
  - `resp_code 43001=2`（text集計では5）
  - `fills=4`
  - fill の `price / size / fee` 0 件数はすべて `0`
  - `max_position_quote_reduce=1639`
  - `max_position_quote_block=637`
  - `max_position=0`
  - `unhedged_exceeded=0`
- 約定内訳:
  - `USDT-FUTURES:QUOTE_ASK` count `2`, sell qty `551`
  - `SPOT:HEDGE` count `2`, buy qty `551`
  - `fills.jsonl` に shutdown `FLATTEN` fill は出ていない。
- shutdown時:
  - flatten前 snapshot: `spot_available=550.454`, `perp_position=-551.0`, `spot_notional=98.68814539`, `flat=false`
  - flatten後 snapshot: `spot_available=0.004`, `perp_position=0.0`, `spot_notional=0.00071714`, `flat=true`
  - `orders.jsonl` には futures market buy `551.0` と spot IOC sell `550.45` の shutdown FLATTEN 注文がある。
- 終了後read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SKYAIUSDT position=0.0`
  - `SPOT SKYAI available=0.004`
  - `SPOT SKYAI frozen=0.0`
- rough:
  - `QUOTE_ASK/HEDGE` rough known net `0.16062251 USDT`
  - `pnl_net_sum=-2.4151776587999993`

### 推論
- 1% bufferで運転中 `max_position` は解消。運用安全性は改善。
- 収益性はまだ不合格。短時間boundedでは hedged position を shutdown flatten するため、basis / funding / taker fee が粗利を上回る。
- shutdown flatten fill が `fills.jsonl` に出ない主因は、finallyで全taskを先にcancelし、private WS / fill monitor停止後にflattenしていたこと。
- bounded収益評価は「稼働中QUOTE/HEDGE」と「終了時FLATTEN」の損益を分けて読む必要がある。

### 実装
- `bot\app.py`
  - shutdown時は先に `risk.halt("shutdown")` で新規quoteを止める。
  - private WS / fill monitor を生かしたまま `_flatten_positions_on_shutdown()` と `_cancel_all_on_shutdown()` を実行し、その後task cancelする順序へ変更。
  - shutdown flatten後に `oms.drain_fills_once()` を呼び、`shutdown_fill_drain_done` をログ出力。
- `bot\oms\oms.py`
  - `drain_fills_once()` を追加。
  - `store.fill.find()` の未処理fillを既存parser / dedupe / ingest経路で同期処理。
  - `fill_drain_done` / `fill_drain_failed` をログ出力。
- `tests\test_hedge_ticket_flatten_race.py`
  - shutdown store fill が1回だけ `fills_logger` に落ち、2回目はdedupeされるテストを追加。
- `tests\test_stop_bot_scripts.py`
  - shutdown fill drain と、shutdown flatten前にtask cancelしない順序を固定。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\oms\oms.py bot\app.py tests\test_hedge_ticket_flatten_race.py tests\test_stop_bot_scripts.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ticket_flatten_race.py tests\test_stop_bot_scripts.py`: `30 passed`。
- `config.yaml` 差分なし。

### 未確定点
- shutdown fill drain修正後の live bounded 再確認は未実施。
- 収益化判断には shutdown flatten fill 捕捉後の実損益再測定と、保有/exit方針の再設計が必要。

---

## 2026-06-05 shutdown fill drain確認とSPOT BUY IOC丸め修正

### 観測事実
- commit `2928d13 fix: keep fill monitor alive during shutdown flatten` 後に bounded live を実行。
- `SKYAIUSDT wide18` log: `runtime_logs\live_symbol_SKYAIUSDT_wide18_shutdown_fill_drain_15min_20260605_081030`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_fill_drain_done=2`
  - fill `0`
  - 終了後read-only: open orders `0`, futures position `0.0`, spot dust `0.004`
- `SKYAIUSDT wide14` log: `runtime_logs\live_symbol_SKYAIUSDT_wide14_shutdown_fill_drain_probe_15min_20260605_082800`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_fill_drain_done=2`
  - fill `0`
  - `edge_negative_total=1112` で注文0
  - 終了後read-only: open orders `0`, futures position `0.0`, spot dust `0.004`
- public ticker scan:
  - 初期spot在庫なしでは BID優位銘柄は `spot_hedge_sell_available_block` 対象。
  - ASK優位かつ流動性高めの候補として `SAHARAUSDT` を選択。
- `SAHARAUSDT wide18` log: `runtime_logs\live_symbol_SAHARAUSDT_wide18_shutdown_fill_drain_probe_15min_20260605_084430`
  - 起動前read-only: open orders `0`, futures position `0.0`, spot available `0.0`
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `resp_code 22002=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_fill_drain_done=2`
  - fills `2`
  - `USDT-FUTURES:QUOTE_ASK` sell qty `1800`, price `0.03338`, fee `-0.00841176`
  - `USDT-FUTURES:FLATTEN` buy qty `1800`, price `0.03335`, fee `-0.0252126`
  - `SPOT:HEDGE` fill は出ていない。
  - 終了後read-only: open orders `0`, futures position `0.0`, spot available `0.0`
- 該当HEDGE注文:
  - `price_before_round=0.03323661`
  - `price_after_round=0.03323`
  - `side=buy`
  - `force=ioc`
  - `resp_code=00000`
  - fillなし、後続で `hedge_pending_expired`。

### 推論
- `SAHARAUSDT` は約定発生率は改善したが、spot hedge が未成立。
- 主因候補は SPOT BUY IOC の価格丸め。買いヘッジの aggressive price を floor しており、tick丸めで板下に落ちる。
- SPOT SELL IOC は floor が aggressive 方向なので維持。SPOT BUY IOC は ceil が必要。

### 実装
- `bot\exchange\constraints.py`
  - `quantize_spot_price(price, side, constraints)` を追加。
  - SPOT BUY は `ROUND_CEILING`、SPOT SELL は `ROUND_FLOOR`。
- `bot\oms\oms.py`
  - SPOT注文価格丸めを `quantize_spot_price()` に変更。
- `bot\exchange\bitget_gateway.py`
  - gateway側のSPOT payload丸めも `quantize_spot_price()` に変更。
- `tests\test_hedge_ticket_flatten_race.py`
  - OMS / gateway の SPOT BUY は `2105.25` へ上丸め。
  - OMS / gateway の SPOT SELL は `2105.24` へ下丸め。

### 検証
- `.venv\Scripts\python.exe -m py_compile bot\exchange\constraints.py bot\exchange\bitget_gateway.py bot\oms\oms.py tests\test_hedge_ticket_flatten_race.py`: pass。
- `.venv\Scripts\python.exe -m pytest -q tests\test_hedge_ticket_flatten_race.py tests\test_perp_price_rounding.py`: `31 passed`。
- `config.yaml` 差分なし。

### 未確定点
- SPOT BUY IOC丸め修正後の live bounded 再確認は未実施。
- `SAHARAUSDT` の実 `QUOTE_ASK -> SPOT:HEDGE` 成立確認が次のP0。

---

## 2026-06-05 SPOT BUY IOC丸め修正後 SAHARAUSDT live 15分確認

### 観測事実
- commit `ce8158e fix: round spot buy ioc hedge price up` 後に `SAHARAUSDT wide18` live bounded 15分を実行。
- log: `runtime_logs\live_symbol_SAHARAUSDT_wide18_spot_buy_roundup_15min_20260605_090405`
- 起動前read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SAHARAUSDT position=0.0`
  - `SPOT SAHARA available=0.0`
  - `SPOT SAHARA frozen=0.0`
- safety:
  - `order_reject=0`
  - `fill_parse_warning=0`
  - `resp_code 22002=0`
  - `shutdown_cancel_all_done=1`
  - `shutdown_cancel_all_failed=0`
  - `shutdown_flatten_positions_done=1`
  - `shutdown_flatten_positions_failed=0`
  - `shutdown_fill_drain_done=2`
  - `hedge_pending_expired=0`
  - `ticket_done=1`
- fills:
  - `USDT-FUTURES:QUOTE_ASK` sell qty `1806`, price `0.03327`, fee `-0.00841198 USDT`
  - `SPOT:HEDGE` buy qty `1806`, price `0.03319`, fee `1.806 SAHARA`
  - `USDT-FUTURES:FLATTEN` buy qty `1806`, price `0.03322`, fee `-0.02519803 USDT`
  - `SPOT:FLATTEN` sell qty `1804.19`, price `0.03308`, fee `0.05968261 USDT`
- rough:
  - `QUOTE_ASK -> SPOT:HEDGE` gross `0.14448 USDT`
  - known net `0.13606802 USDT`（spot feeはbase coin）
  - `pnl_net_sum=-1.7987736634599671`
- 終了後read-only:
  - `SPOT open orders=0`
  - `Futures open orders=0`
  - `Futures SAHARAUSDT position=0.0`
  - `SPOT SAHARA available=0.004`
  - `SPOT SAHARA frozen=0.0`

### 推論
- SPOT BUY IOC 丸め修正は有効。実 `QUOTE_ASK -> SPOT:HEDGE` が成立した。
- bounded 15分の収益性は不合格。entry後に hedged carry position を持ち、終了時 `FLATTEN` で spot 側を低値売却して粗利を消している。
- この戦略は短時間boundedのshutdown flatten損益と、funding carryの本来評価を分ける必要がある。
- 次の収益化P0は「保有してfundingを取りに行く条件」と「途中exit/flatten条件」を分離して評価すること。

### 未確定点
- funding時刻を跨ぐ実測は未実施。
- shutdown flattenを除外したrealized carry成績は未確定。
- 常駐/24hへ進むには、exit条件またはfunding-window評価条件の明確化が必要。
