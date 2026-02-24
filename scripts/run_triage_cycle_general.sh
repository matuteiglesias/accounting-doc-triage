#!/usr/bin/env bash
set -euo pipefail

# scripts/run_triage_cycle_general.sh
# Generalized triage-run driver
# Usage examples:
#  ./scripts/run_triage_cycle_general.sh --flow ./flows/general --inbox 1_Input_Raw/00_inbox
#  ./scripts/run_triage_cycle_general.sh --flow ./flows/payment --inbox moved/payment --apply --backup
#  ./scripts/run_triage_cycle_general.sh --data-root ~/RAG_Sync/Accounting --flow ./flows/payment

ENGINE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Data root: where 1_Input_Raw/, moved/, 4_Analysis_Workflows/, backups/ live.
# For now we keep your existing data layout under ~/RAG_Sync/Accounting by default.
DEFAULT_DATA_ROOT="$HOME/RAG_Sync/Accounting"
DATA_ROOT=""

DEFAULT_INDEXER="$ENGINE_ROOT/scripts/triage_indexer.py"
DEFAULT_MOVE_SCRIPT="$ENGINE_ROOT/scripts/move_triage_files.py"
DEFAULT_FLOW_DIR="$ENGINE_ROOT/flows/general"
DEFAULT_INBOX_REL="1_Input_Raw/00_inbox"

# defaults / CLI parsing
FLOW_DIR=""
INBOX=""
INDEXER=""
MOVE_SCRIPT=""
APPLY=0
BACKUP=0
WIPE_INBOX=0
PF_ARGS=""

print_usage(){
  cat <<EOF
Usage: $(basename "$0") [options]
  --data-root PATH     Data root folder (default: $DEFAULT_DATA_ROOT if it exists, else $ENGINE_ROOT)
  --flow PATH          PromptFlow flow folder (default: $DEFAULT_FLOW_DIR)
  --inbox PATH         Inbox folder to index (default: <data-root>/$DEFAULT_INBOX_REL)
  --indexer PATH       indexer script (default: $DEFAULT_INDEXER)
  --move-script PATH   move script (default: $DEFAULT_MOVE_SCRIPT)
  --apply              Apply moves (default: dry-run)
  --backup             Create backup of inbox prior to applying (only used with --apply)
  --wipe-inbox         Wipe remaining inbox files after apply (dangerous)
  --pf-args "..."      Extra args passed to pf run create (quoted)
  -h, --help           Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --data-root) DATA_ROOT="$2"; shift 2;;
    --flow) FLOW_DIR="$2"; shift 2;;
    --inbox) INBOX="$2"; shift 2;;
    --indexer) INDEXER="$2"; shift 2;;
    --move-script) MOVE_SCRIPT="$2"; shift 2;;
    --apply) APPLY=1; shift;;
    --backup) BACKUP=1; shift;;
    --wipe-inbox) WIPE_INBOX=1; shift;;
    --pf-args) PF_ARGS="$2"; shift 2;;
    -h|--help) print_usage; exit 0;;
    *) echo "Unknown arg: $1"; print_usage; exit 2;;
  esac
done

# Resolve DATA_ROOT default
if [ -z "${DATA_ROOT:-}" ]; then
  if [ -d "$DEFAULT_DATA_ROOT/1_Input_Raw" ]; then
    DATA_ROOT="$DEFAULT_DATA_ROOT"
  else
    # fallback keeps behavior usable even if you run the engine inside a combined repo
    DATA_ROOT="$ENGINE_ROOT"
  fi
fi

FLOW_DIR="${FLOW_DIR:-$DEFAULT_FLOW_DIR}"
INDEXER="${INDEXER:-$DEFAULT_INDEXER}"
MOVE_SCRIPT="${MOVE_SCRIPT:-$DEFAULT_MOVE_SCRIPT}"

# Inbox: if user didn't pass --inbox, default to <data-root>/1_Input_Raw/00_inbox
if [ -z "${INBOX:-}" ]; then
  INBOX="$DATA_ROOT/$DEFAULT_INBOX_REL"
fi

# normalize absolute
FLOW_DIR="$(realpath "$FLOW_DIR")"
DATA_ROOT="$(realpath "$DATA_ROOT")"
INBOX="$(realpath "$INBOX")"
INDEXER="$(realpath "$INDEXER")"
MOVE_SCRIPT="$(realpath "$MOVE_SCRIPT")"

FLOW_NAME="$(basename "$FLOW_DIR")"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

WORK_RUN_DIR="$DATA_ROOT/4_Analysis_Workflows/triage_runs/$FLOW_NAME/run_$TS"
PF_ARCHIVE_DIR="$DATA_ROOT/4_Analysis_Workflows/pf_runs/$FLOW_NAME/run_$TS"

mkdir -p "$WORK_RUN_DIR"
mkdir -p "$PF_ARCHIVE_DIR"

RAW_IN="$WORK_RUN_DIR/input.jsonl"
RAW_IN_LATEST="$DATA_ROOT/4_Analysis_Workflows/triage_input_latest.jsonl"

echo "ENGINE_ROOT: $ENGINE_ROOT"
echo "DATA_ROOT: $DATA_ROOT"
echo "FLOW_DIR: $FLOW_DIR"
echo "FLOW_NAME: $FLOW_NAME"
echo "INBOX: $INBOX"
echo "INDEXER: $INDEXER"
echo "MOVE_SCRIPT: $MOVE_SCRIPT"
echo "WORK_RUN_DIR: $WORK_RUN_DIR"
echo "RAW_IN: $RAW_IN"
echo "RAW_IN_LATEST: $RAW_IN_LATEST"
echo "PF_ARCHIVE_DIR: $PF_ARCHIVE_DIR"
echo "APPLY: $APPLY  BACKUP: $BACKUP  WIPE_INBOX: $WIPE_INBOX"
echo

# 1) run indexer -> timestamped file
# IMPORTANT: run from DATA_ROOT so any relative paths inside JSONL are relative to DATA_ROOT.
echo "1) Re-index inbox -> $RAW_IN"
(
  cd "$DATA_ROOT"
  python3 "$INDEXER" --input-dir "$INBOX" --out "$RAW_IN"
) || {
  echo "indexer failed; check $INDEXER"
  exit 2
}
# link a stable 'latest' for convenience
ln -sf "$(realpath "$RAW_IN")" "$RAW_IN_LATEST"

# quick guard: abort if zero lines
lines=$(wc -l < "$RAW_IN" 2>/dev/null || echo 0)
if [ "$lines" -eq 0 ]; then
  echo "No input records found in $RAW_IN (lines=0). Aborting PF submit."
  echo "Check the inbox ($INBOX) and indexer ($INDEXER) before re-running."
  exit 0
fi
echo "Indexed $lines records."

# 2) optional backup of inbox (before apply)
if [ "$BACKUP" -eq 1 ] && [ "$APPLY" -eq 1 ]; then
  echo "Creating backup of inbox..."
  BACKUP_TAR="$DATA_ROOT/backups/inbox_backup_${TS}.tar.gz"
  mkdir -p "$DATA_ROOT/backups"
  (cd "$DATA_ROOT" && tar -czf "$BACKUP_TAR" --transform="s|^|inbox_${TS}/|" -C "$(dirname "$INBOX")" "$(basename "$INBOX")")
  echo "Backup created at: $BACKUP_TAR"
fi

# 3) run PromptFlow from inside the flow dir so relative sources resolve
echo "3) Running PromptFlow for flow: $FLOW_DIR"
echo "   NOTE: ensure OPENAI/AZURE creds accessible to PF (env or init.json)"

# create a timestamp before we call pf, to find the exact run folder after PF returns
PF_RUN_START_EPOCH=$(date +%s)

pushd "$FLOW_DIR" >/dev/null
set +e
pf_cmd=(python -m promptflow._cli.pf run create --flow . --data "$RAW_IN")
if [ -n "$PF_ARGS" ]; then
  pf_cmd+=( $PF_ARGS )
fi
echo "   Command: ${pf_cmd[*]}"
"${pf_cmd[@]}"
PF_RC=$?
set -e
popd >/dev/null

if [ "$PF_RC" -ne 0 ]; then
  echo "PromptFlow returned non-zero ($PF_RC). Check PF logs under ~/.promptflow/.runs/*"
  # continue to try to archive whatever PF produced (if anything)
fi

# 4) find the PF run directory created after PF_RUN_START_EPOCH (exact match)
PF_RUN_DIR=$(find ~/.promptflow/.runs -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
  | awk -v t="$PF_RUN_START_EPOCH" '$1 >= t {print $2}' \
  | sort -n | tail -n 1)

if [ -z "$PF_RUN_DIR" ]; then
  # fallback to most recent run if none newer than start time
  PF_RUN_DIR=$(ls -dt ~/.promptflow/.runs/* 2>/dev/null | head -n1 || true)
fi

if [ -z "$PF_RUN_DIR" ]; then
  echo "No PromptFlow run directory found under ~/.promptflow/.runs/. Aborting."
  exit 1
fi

echo "Found PF run dir: $PF_RUN_DIR"

# copy PF outputs to PF_ARCHIVE_DIR (keeps input + output + pf logs)
mkdir -p "$PF_ARCHIVE_DIR"
cp -v "$RAW_IN" "$PF_ARCHIVE_DIR/input.jsonl" || true
if [ -f "$PF_RUN_DIR/flow_outputs/output.jsonl" ]; then
  cp -v "$PF_RUN_DIR/flow_outputs/output.jsonl" "$PF_ARCHIVE_DIR/output.jsonl"
else
  echo "No output.jsonl found inside $PF_RUN_DIR/flow_outputs/ (PF job may have failed)."
fi
if [ -f "$PF_RUN_DIR/logs.txt" ]; then
  cp -v "$PF_RUN_DIR/logs.txt" "$PF_ARCHIVE_DIR/pf_logs_tail.txt"
fi

echo "PF run archived into: $PF_ARCHIVE_DIR"

# 5) run mover (dry-run unless --apply)
PF_OUTPUT_PATH="$PF_ARCHIVE_DIR/output.jsonl"
if [ ! -f "$PF_OUTPUT_PATH" ]; then
  echo "No PF output found at $PF_OUTPUT_PATH. Aborting mover step."
  exit 0
fi

echo "5) Running mover"
move_cmd=(python3 "$MOVE_SCRIPT" --pf "$PF_OUTPUT_PATH" --orig "$RAW_IN")
if [ "$APPLY" -eq 1 ]; then
  move_cmd+=(--apply)
  if [ "$BACKUP" -eq 1 ]; then
    move_cmd+=(--backup)
  fi
  if [ "$WIPE_INBOX" -eq 1 ]; then
    move_cmd+=(--wipe-inbox)
  fi
fi
echo "   Command (run from DATA_ROOT): ${move_cmd[*]}"
(
  cd "$DATA_ROOT"
  "${move_cmd[@]}"
)

echo
echo "Done. Logs: $DATA_ROOT/moved/move_report_*.log"
echo "PF archive: $PF_ARCHIVE_DIR"
