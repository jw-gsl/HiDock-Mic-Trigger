#!/usr/bin/env bash
# Collect the reviewed-transcript eval corpus (sidecars + the audio they
# reference) into one tarball for transfer to an NVIDIA host, where
# `~/nemo-diar/run_eval.py --transcripts-dir <extracted>` can score
# nemotron vs the other backends.
#
# Usage:  ./collect-nemotron-eval-corpus.sh [output.tar.gz]
# Default output: ~/nemotron-eval-corpus.tar.gz
#
# Real audio + real voices go in this file: it is GROUND TRUTH for a
# local benchmark, and it stays on your own machines. Nothing uploads
# anywhere.
set -euo pipefail

SRC="${HIDOCK_RAW_TRANSCRIPTS:-$HOME/HiDock/Raw Transcripts}"
OUT="${1:-$HOME/nemotron-eval-corpus.tar.gz}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

[[ -d "$SRC" ]] || { echo "not found: $SRC (override with HIDOCK_RAW_TRANSCRIPTS=...)" >&2; exit 1; }

copied=0
for sidecar in "$SRC"/*_diarized.json; do
  [[ -e "$sidecar" ]] || continue
  # only corpus cases with >=2 verified speakers; the harness re-applies
  # its own filter, we just pre-trim the obvious rejects
  verified=$(python3 -c "
import json,sys
try:
    d=json.load(open('$sidecar'))
except Exception: sys.exit(0)
m=d.get('speaker_meta') or {}
print(sum(1 for v in m.values() if (v or {}).get('verified') is True))
" 2>/dev/null || echo 0)
  (( verified >= 2 )) || continue

  cp "$sidecar" "$STAGE/"
  audio=$(python3 -c "
import json
d=json.load(open('$sidecar'))
print(d.get('audio_file',''))
" 2>/dev/null || true)
  if [[ -n "$audio" && -f "$audio" ]]; then
    cp "$audio" "$STAGE/"
  else
    # sidecar without audio is useless to the harness — undo the copy
    rm "$STAGE/$(basename "$sidecar")"
    continue
  fi
  copied=$((copied + 1))
done

if (( copied == 0 )); then
  echo "no usable reviewed cases (need >=2 verified speakers + audio present)" >&2
  exit 1
fi

tar -czf "$OUT" -C "$STAGE" .
chmod 600 "$OUT"
echo "collected $copied case(s) -> $OUT"
echo "transfer:  scp $OUT <nvidia-host>:~/HiDock-eval/"
echo "on host:   mkdir -p ~/HiDock-eval/corpus && tar -xzf ~/HiDock-eval/$(basename "$OUT") -C ~/HiDock-eval/corpus"
