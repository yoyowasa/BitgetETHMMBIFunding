# 別 PC への移管 / 常駐稼働ガイド

## 1. 必須要件

| 項目 | 要件 |
|---|---|
| OS | Windows 10/11 (Linux でも可、PowerShell スクリプトは bash 化必要) |
| Python | **3.11.x**（pybotters 互換性の確認済バージョン） |
| 必須空き容量 | 5 GB（logs 蓄積用） |
| 常時接続 | 安定回線。WS 切断時は自動再接続するが、頻繁な切断は機会損 |
| 時刻同期 | NTP 必須（funding 時刻基準が UTC） |
| 電源 | 24/7 常駐するなら UPS 推奨 |

## 2. 環境構築（別 PC 側）

### 2.1 Python と git 準備
```powershell
# Python 3.11 インストール (公式 installer or winget)
winget install Python.Python.3.11
# 確認
python --version  # 3.11.x が出ること
git --version
```

### 2.2 リポジトリ取得
```powershell
cd C:\BOT
git clone <repo-url> BitgetETHMMBIFunding
cd BitgetETHMMBIFunding
```

### 2.3 venv + 依存
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
# matplotlib（plot_pnl.py 用、任意）
pip install matplotlib
```

WDAC でブロックされる場合: 別 venv パス（例 `C:\Users\<user>\dev\BitgetETHMMBIFunding\.venv`）に作り直すか、ポリシー除外設定。

### 2.4 設定ファイル
```powershell
copy config.example.yaml config.yaml
```

`config.yaml` を編集して **本機の最終形**に合わせる（下記 セクション 3 参照）。

### 2.5 .env 設定
```powershell
copy .env.example .env
notepad .env
```

最低限以下を設定:
```
BITGET_API_KEY=...
BITGET_API_SECRET=...
BITGET_API_PASSPHRASE=...
DRY_RUN=1
```

**注意**: API キーは新 PC 用に Bitget 側で**新規発行推奨**。旧 PC のキーは無効化。

### 2.6 動作確認
```powershell
# テスト
python -m pytest -q
# DRY_RUN 30分動作確認
.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --duration-sec 1800
# PnL 確認（要 matplotlib）
python scripts\plot_pnl.py
```

`reports/pnl_plots/` の最新 PNG を見て、本機と同等の挙動確認。

---

## 3. config.yaml 本機最終形（移管時参照）

主要パラメータ（2026-04-27 時点の本機検証済値）:

```yaml
risk:
  max_unhedged_sec: 2.0
  max_unhedged_notional: 200
  max_position_notional: 2000
  cooldown_sec: 30
  reject_streak_limit: 3

strategy:
  enable_only_positive_funding: true
  min_funding_rate: 0.0          # funding>0 なら常時 quote（稼働率最大化）
  target_notional: 500
  base_half_spread_bps: 18.0     # 新 cost 24.8bps 前提で +9.78bps EV
  min_half_spread_bps: 18.0
  quote_fade_policy: threshold_8bps
  cancel_aggressive_policy: current
  cancel_aggressive_scope: active_quote_only
  cancel_aggressive_quality_filter: fresh_active_quote_proximity
  one_sided_quote_policy: current
  tfi_fade_policy: disabled
  reprice_threshold_bps: 1.0
  adverse_buffer_bps: 2.0
  dry_run: true                  # DRY_RUN=0 移行時に false へ

cost:
  fee_maker_perp_bps: 1.4        # 実測 mix VIP3 相当
  fee_taker_spot_bps: 10.0       # spot VIP0
  slippage_bps: 2.0
```

VIP tier は別 PC でも同じアカウントなら同値。新規アカウントの場合は `scripts/check_vip_tier.py` で再確認必須。

---

## 4. DRY_RUN=0 移行手順（資金確保後）

### 4.1 事前チェック
- [ ] `.env` の API キーが正しく設定済（新発行推奨）
- [ ] Bitget アカウントに USDT 入金済（最低 `max_position_notional × 2` = 4000 USDT 推奨）
- [ ] perp は USDT-FUTURES isolated margin、spot は ETHUSDT で取引可
- [ ] `scripts/check_vip_tier.py` で fee_maker_perp / fee_taker_spot を再確認、ズレあれば config.yaml.cost を修正
- [ ] `target_notional=100`、`max_position_notional=200`、`max_unhedged_notional=50` に**初回は縮小**

### 4.2 微小ロット初回 live
```powershell
# config.yaml の strategy.dry_run: false に変更
# 30 分 live
.\.venv\Scripts\python.exe scripts\run_bot_for_duration.py --duration-sec 1800
```

確認:
- pnl.jsonl の net_pnl 符号
- orders.jsonl の fill 件数、quote_fill_rate
- system.jsonl の halted/reject 有無
- 残高変動（Bitget Web）

### 4.3 段階拡大
- 30 分 OK → 1 時間
- 1 時間 OK → 6 時間
- 6 時間 OK → target_notional を 200, 500, 1000 と段階拡大
- 各段で pnl.jsonl と plot_pnl.py で PnL 推移確認

---

## 5. 常駐稼働

### 5.1 起動 (常駐モード)
```powershell
.\scripts\start_bot.ps1
```

このスクリプトは bot を別ウィンドウで起動し、クラッシュ時 30 秒後に自動再起動する。

### 5.2 停止
```powershell
.\scripts\stop_bot.ps1
```

graceful stop（cancel_all + position flatten 試行）。

### 5.3 Windows Task Scheduler 化（PC 起動時自動開始）
1. タスクスケジューラ → タスクの作成
2. トリガー: ログオン時 / システム起動時
3. 操作: プログラム = `powershell.exe`、引数 = `-File C:\BOT\BitgetETHMMBIFunding\scripts\start_bot.ps1`
4. 設定: 「失敗した場合に再起動」「最大3日間」

### 5.4 定期 PnL レポート
日次で `scripts/plot_pnl.py` を実行する Task を作成（任意、確認用）。

---

## 6. 監視

### 6.1 主要ログ
| ファイル | 内容 | 監視ポイント |
|---|---|---|
| `logs/pnl.jsonl` | 1 分集計 PnL | `net_pnl` 累積、`quote_fill_rate`、`adverse_fill_rate` |
| `logs/system.jsonl` | bot 状態 | `halted`, `ws_disconnect`, `preflight_failed` |
| `logs/orders.jsonl` | order/fill | `fill` イベント、`reject` |
| `logs/decision.jsonl` | strategy 判定 | `pre_quote_decision` の `final_block_reason` 偏り |

### 6.2 1 行ヘルスチェック (PowerShell)
```powershell
# 直近 5 分の net_pnl 累計
Get-Content logs\pnl.jsonl -Tail 5 | ForEach-Object { ($_ | ConvertFrom-Json).net_pnl } | Measure-Object -Sum
```

### 6.3 ログローテート
`logs/` は無制限蓄積。週 1 回程度 `logs/archive/<YYYYMMDD>/` へ手動退避推奨。
将来的には `tools/audit.ps1` などで自動化検討。

---

## 7. 緊急停止 / 障害対応

| 症状 | 対応 |
|---|---|
| PnL が想定外マイナス | `stop_bot.ps1` で即停止、Bitget Web で残ポジ手動 flatten |
| 大量 reject | `stop_bot.ps1`、API キー権限・残高確認 |
| WS 切断頻発 | 回線・取引所側障害確認、`risk.controlled_reconnect_grace_sec` 調整 |
| funding negative 化 | 自動停止する設計（`enable_only_positive_funding=true`） |
| プロセスハング | タスクマネージャで python.exe kill → 残ポジ手動確認 |

**緊急 flatten** (CLI):
```powershell
# 全 quote cancel + position close（要実装、現状は bot 停止 → Bitget Web）
```

---

## 8. Git コミット必要項目（移管前）

本機で未コミットの変更:
- `config.yaml`: 本機最終形
- `bot/app.py`: sim_fill 改修（active quote 価格 + fee 反映）
- `bot/config.py`, `bot/strategy/mm_funding.py`, `bot/oms/oms.py`, `bot/exchange/bitget_gateway.py`: 既存変更
- `STATUS.md`: 履歴更新
- `scripts/check_vip_tier.py`: 新規（VIP tier API 確認）
- `scripts/plot_pnl.py`: 新規（PnL 可視化）
- `scripts/analyze_*.py`: 多数の新規分析スクリプト
- `tests/test_*.py`: 新規 4 ファイル
- `MIGRATION.md`: 本ファイル

`.gitignore` で除外: `.env` / `.venv*/` / `logs/` / `runtime_logs/` / `artifacts/` / `__pycache__/`

reports/ は git 管理判断（過去 DRY_RUN レポートを残すなら add、別途バックアップ取るなら ignore 追加）。

---

## 9. 別 PC 移管チェックリスト

### 旧 PC（本機）側
- [ ] 全変更を git commit
- [ ] git push（remote ある場合）
- [ ] config.yaml を別途バックアップ（個別パラメータ調整あれば）
- [ ] `.env` を安全な経路でコピー（USB / パスワードマネージャ等、メールやクラウド平文 NG）
- [ ] reports/ 過去ログは必要なら zip 取得

### 新 PC 側
- [ ] Python 3.11 インストール
- [ ] git clone
- [ ] venv 作成 + 依存インストール
- [ ] テスト pass 確認 (`pytest -q`)
- [ ] config.yaml コピー
- [ ] `.env` コピー（または新規発行 API キーで設定）
- [ ] DRY_RUN=1 で 30 分動作確認
- [ ] PnL plot で本機同等の挙動確認
- [ ] start_bot.ps1 で常駐起動テスト
- [ ] Task Scheduler 登録（自動起動希望時）
- [ ] DRY_RUN=0 移行は資金確認後、セクション 4 手順
