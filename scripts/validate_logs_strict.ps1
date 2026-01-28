param(
  [string]$LogDir = "logs",
  [string]$Out = "artifacts/validate_report.json",
  [string]$RequireFiles = "system*.jsonl,orders*.jsonl,fills*.jsonl,mm_*.jsonl",
  [double]$ControlledGraceSec = 4,
  [int]$HedgeMaxTries = 2
)

$artifactDir = Split-Path -Parent $Out
if ($artifactDir) {
  New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
}

python tools/validate_logs.py $LogDir `
  --json-out $Out `
  --require-files $RequireFiles `
  --require-ticket-events `
  --halt-strict `
  --controlled-grace-sec $ControlledGraceSec `
  --hedge-max-tries $HedgeMaxTries
