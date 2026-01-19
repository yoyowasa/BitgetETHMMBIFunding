param(
  [string]$LogDir = "logs",
  [string]$Out = "artifacts/validate_report.json"
)

$require = "system*.jsonl,orders*.jsonl,fills*.jsonl,mm_*.jsonl"

$artifactDir = Split-Path -Parent $Out
if ($artifactDir) {
  New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
}

python tools/validate_logs.py $LogDir `
  --json-out $Out `
  --require-files $require `
  --require-ticket-events `
  --halt-strict `
  --controlled-grace-sec 6 `
  --hedge-max-tries 2
