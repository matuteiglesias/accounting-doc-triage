#!/usr/bin/env python3
"""
concat_dedupe_jsonl.py

Concatenate multiple JSONL files, dedupe by "id" keeping the last-seen record (chronologically by run token or mtime),
and write a single deduplicated JSONL.

Usage examples:

# defaults will match your two run dirs under 4_Analysis_Workflows/pf_runs
python scripts/concat_dedupe_jsonl.py -o artifacts/jsonl/combined_dedup.jsonl

# or pass explicit globs
python scripts/concat_dedupe_jsonl.py \
  "4_Analysis_Workflows/pf_runs/flow_payment/run_*/output.jsonl" \
  "4_Analysis_Workflows/pf_runs/flow_statement/run_*/output.jsonl" \
  -o artifacts/jsonl/combined_dedup.jsonl

# if memory is a concern, add --check-only to show counts and estimated memory (no write)
python scripts/concat_dedupe_jsonl.py -o - --check-only
"""
import argparse
import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

RUN_TS_RE = re.compile(r"run_(\d{8}T\d{6}Z)")  # matches run_20251028T045313Z

def extract_run_ts_from_path(p: str):
    m = RUN_TS_RE.search(p)
    if m:
        ts = m.group(1)  # 20251028T045313Z
        # parse to datetime (UTC)
        try:
            return datetime.strptime(ts, "%Y%m%dT%H%M%SZ")
        except Exception:
            return None
    return None

def find_files(globs: List[str]) -> List[Path]:
    files = []
    for g in globs:
        found = sorted(glob.glob(g))
        files.extend(found)
    # uniq and keep order of discovery
    seen = set()
    out = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(Path(f))
    return out

def order_files_chronological(paths: List[Path]) -> List[Path]:
    def key_fn(p: Path):
        ts = extract_run_ts_from_path(str(p))
        if ts:
            return (0, ts)  # prefer parsed run timestamp
        else:
            # fallback to mtime
            try:
                return (1, datetime.fromtimestamp(p.stat().st_mtime))
            except Exception:
                return (2, datetime.min)
    return sorted(paths, key=key_fn)

def concat_and_dedupe(paths: List[Path], id_field: str = "id", check_only: bool = False):
    """
    Process files in chronological order (oldest -> newest). Keep last seen record per id.
    Returns a dict id -> (raw_line, seq_index)
    """
    store = {}  # id -> (raw_line, seq_index)
    seq = 0
    total_lines = 0
    for p in paths:
        if not p.exists():
            continue
        # show progress-ish
        print(f"Processing: {p} (size={p.stat().st_size} bytes)")
        with p.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                total_lines += 1
                # fast parse for id field without full JSON if possible
                try:
                    obj = json.loads(raw)
                except Exception:
                    # if a line is invalid json, skip but warn
                    print(f"Warning: skipping invalid json line in {p}")
                    continue
                if id_field not in obj:
                    # skip entries without id (or you may want to include them)
                    print(f"Warning: line in {p} has no '{id_field}', skipping")
                    continue
                doc_id = obj[id_field]
                seq += 1
                # overwrite: keep last-seen
                store[doc_id] = (raw, seq)
    print(f"Processed total lines: {total_lines}; unique ids kept: {len(store)}")
    if check_only:
        return store, total_lines
    return store, total_lines

def write_output(store: dict, out_path: Path):
    # order by seq to reflect the chronological last-seen order
    items = sorted(store.items(), key=lambda kv: kv[1][1])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for _id, (raw, seq) in items:
            fh.write(raw.rstrip("\n") + "\n")
    print(f"Wrote {len(items)} records to {out_path}")

def main(argv=None):
    p = argparse.ArgumentParser(description="Concatenate JSONL and dedupe by id keeping last seen chronologically.")
    p.add_argument("globs", nargs="*", default=[
        "4_Analysis_Workflows/pf_runs/flow_payment/run_*/output.jsonl",
        "4_Analysis_Workflows/pf_runs/flow_statement/run_*/output.jsonl"
    ], help="One or more glob patterns matching jsonl files")
    p.add_argument("-o", "--output", default="artifacts/jsonl/combined_dedup.jsonl", help="Output JSONL path (use '-' for stdout check-only)")
    p.add_argument("--id-field", default="id", help="Field name used as unique id in JSON objects")
    p.add_argument("--check-only", action="store_true", help="Do not write output; only report counts and memory estimate")
    args = p.parse_args(argv)

    files = find_files(args.globs)
    if not files:
        print("No files matched the given patterns.")
        return 2

    ordered = order_files_chronological(files)
    print("Files to process (chronological order):")
    for f in ordered:
        ts = extract_run_ts_from_path(str(f))
        tsinfo = ts.isoformat() if ts else datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        print("  ", f, tsinfo)

    store, total_lines = concat_and_dedupe(ordered, id_field=args.id_field, check_only=args.check_only)

    if args.check_only:
        print("Check-only mode. Exiting after counting.")
        print(f"Total lines seen: {total_lines}; unique ids: {len(store)}")
        return 0

    outp = Path(args.output) if args.output != "-" else None
    if outp is None:
        # write to stdout
        for _id, (raw, seq) in sorted(store.items(), key=lambda kv: kv[1][1]):
            print(raw)
        return 0

    write_output(store, outp)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
