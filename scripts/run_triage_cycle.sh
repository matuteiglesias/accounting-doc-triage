#!/usr/bin/env bash
set -euo pipefail

# scripts/run_triage_cycle.sh
# Usage:
#   ./scripts/run_triage_cycle.sh        # dry-run only
#   ./scripts/run_triage_cycle.sh --apply   # perform moves + backups + quarantine

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

INBOX="1_Input_Raw/00_inbox"
PF_OUT="4_Analysis_Workflows/triage_output_from_pf.jsonl"
ORIG="4_Analysis_Workflows/triage_input_with_id.jsonl"
RAW_IN="4_Analysis_Workflows/triage_input.jsonl"

APPLY=0
if [ "${1-}" = "--apply" ] || [ "${1-}" = "-a" ]; then
  APPLY=1
fi

echo "ROOT: $ROOT"
echo "INBOX: $INBOX"
echo "PF_OUT: $PF_OUT"
echo "ORIG: $ORIG"
echo "RAW_IN: $RAW_IN"
echo

echo "1) Re-index inbox..."
python3 triage_indexer.py --input-dir "$INBOX" --out "$RAW_IN"

echo
echo "2) Ensure id = id ..."
jq -c '.id = (.id // .id // "")' "$RAW_IN" > "$ORIG"

echo
echo "3) Run PromptFlow (make sure OPENAI/Azure creds are set in your env or init.json)."
echo "   Note: this may spin up the local PF service and take a little while."
python -m promptflow._cli.pf run create --flow ./flow/ --data "$ORIG"

echo
echo "4) Copy latest PF output into repo (if present)..."
latest=$(ls -dt ~/.promptflow/.runs/*/flow_outputs/output.jsonl 2>/dev/null | head -n1 || true)
if [ -n "$latest" ]; then
  cp -v "$latest" "$PF_OUT"
  echo "copied $latest -> $PF_OUT"
else
  echo "No promptflow output found; check pf logs. Exiting."
  exit 1
fi

echo
if [ "$APPLY" -eq 0 ]; then
  echo "5) Dry-run move (inspect moved/move_report_*.log) -- no files will be moved."
  python move_triage_files.py --pf "$PF_OUT" --orig "$ORIG"
  echo
  echo "This was a dry-run. To actually move files, re-run with --apply"
  echo "Example:"
  echo "  ./scripts/run_triage_cycle.sh --apply"
  exit 0
fi

# APPLY path: real run
echo "5) APPLY MODE: creating backup and applying moves"
python move_triage_files.py --pf "$PF_OUT" --orig "$ORIG" --apply --backup

echo
echo "6) Quarantine any remaining files in the inbox (to moved/quarantine/<ts>)"
if [ -f "$ROOT/quarantine_inbox.py" ]; then
  python quarantine_inbox.py --apply
else
  echo "quarantine_inbox.py not found in repo root. Creating a minimal quarantine move now..."
  TS=$(date +%Y%m%dT%H%M%S)
  QUAR="$ROOT/moved/quarantine/$TS"
  mkdir -p "$QUAR"
  # move everything remaining in inbox into quarantine folder (preserve filenames, avoid overwrites)
  find "$INBOX" -maxdepth 1 -type f -print0 | while IFS= read -r -d $'\0' f; do
    bn=$(basename "$f")
    target="$QUAR/$bn"
    if [ -e "$target" ]; then
      target="$QUAR/${bn%.*}_$TS.${bn##*.}"
    fi
    mv -v "$f" "$target"
  done
  echo "Minimal quarantine completed: $QUAR"
fi

echo
echo "Done. Check logs:"
echo "  moved/  -> moved/move_report_*.log and moved/quarantine/"
echo "  PF run logs: ~/.promptflow/.runs/*/logs.txt"
