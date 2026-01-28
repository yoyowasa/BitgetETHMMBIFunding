#!/usr/bin/env bash
set -euo pipefail

die() {
  # エラー理由を標準エラーに出して終了する
  echo "[run_real_logs] ERROR: $*" >&2
  exit 1
}

require_env() {
  # 指定した環境変数が空なら止める
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    die "env '${name}' is required"
  fi
}

write_meta() {
  # 実行のメタ情報をjsonに保存する（あとで追跡しやすくする）
  local meta_path="$1"
  local run_id="$2"
  local log_dir="$3"
  local cmd_string="$4"
  local ts_utc="$5"
  local git_sha="$6"

  cat > "${meta_path}" <<EOF
{"run_id":"${run_id}","ts_utc":"${ts_utc}","log_dir":"${log_dir}","cmd":"${cmd_string}","git_sha":"${git_sha}"}
EOF
}

build_cmd() {
  # 実行コマンドを組み立てる（引数優先、無ければREAL_LOG_CMDを使う）
  if [[ "$#" -gt 0 ]]; then
    echo "$*"
    return 0
  fi
  require_env "REAL_LOG_CMD"
  echo "${REAL_LOG_CMD}"
}

LOG_DIR="${LOG_DIR:-${LOG_PATH:-logs}}" # LOG_DIR優先、LOG_PATHフォールバック、既定logs
mkdir -p "${LOG_DIR}" # ログ保存先を必ず作る

DRY_RUN_VALUE="${DRY_RUN:-1}" # 既定は安全側（疑似運用）
if [[ "${DRY_RUN_VALUE}" == "0" ]]; then
  # 実運用（DRY_RUN=0）の誤爆防止：明示許可が無いと止める
  if [[ "${REAL_RUN_OK:-}" != "1" ]]; then
    die "DRY_RUN=0 detected. Set REAL_RUN_OK=1 to allow real run."
  fi
fi

TS_UTC="$(date -u +"%Y%m%dT%H%M%SZ")" # 実行時刻（UTC）
RUN_ID="${RUN_ID:-real_${TS_UTC}_$$}" # 実行ID（上書き可）
OUT_LOG="${LOG_DIR}/real_run_${RUN_ID}.log" # 実行ログ
META_JSON="${LOG_DIR}/real_run_${RUN_ID}.meta.json" # メタ情報

GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo "unknown")" # git sha（無ければunknown）

CMD_STRING="$(build_cmd "$@")" # 実行コマンド文字列
write_meta "${META_JSON}" "${RUN_ID}" "${LOG_DIR}" "${CMD_STRING}" "${TS_UTC}" "${GIT_SHA}" # メタ情報を書き出す

echo "[run_real_logs] LOG_DIR=${LOG_DIR}" | tee -a "${OUT_LOG}" # 実行環境をログに残す
echo "[run_real_logs] RUN_ID=${RUN_ID}" | tee -a "${OUT_LOG}" # 実行IDをログに残す
echo "[run_real_logs] DRY_RUN=${DRY_RUN_VALUE}" | tee -a "${OUT_LOG}" # DRY_RUNをログに残す
echo "[run_real_logs] CMD=${CMD_STRING}" | tee -a "${OUT_LOG}" # 実行コマンドをログに残す

bash -lc "${CMD_STRING}" 2>&1 | tee -a "${OUT_LOG}" # コマンドを実行し、出力をファイルへ保存する
