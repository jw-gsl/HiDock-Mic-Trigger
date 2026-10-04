# Remote Nemotron 3 Diarization — architecture & operations

Research + build date: 2026-10-04
Sources: https://huggingface.co/blog/nvidia/nemotron-diarization ,
https://huggingface.co/nvidia/Nemotron-3-Diarization (model card),
https://www.baseten.co/blog/nvidia-nemotron-3-diarization/

## What it is

NVIDIA Nemotron 3 Diarization — open-weight 100M-param end-to-end
diarizer (Sortformer successor). Up to 8 speakers, arrival-time-ordered
labels (stable across chunks), #1 on Voice Arena Diarization-Bench
(14.72% DER with overlap, ~24% relative better than the next system).
Licence **OpenMDW 1.1** — commercial use, no HF licence gate (unlike
pyannote), so unlike ReDimNet2 (CC BY-NC-SA) it could ship in a build.

## Why remote

The model runs only on NVIDIA GPUs (Ampere→Blackwell; the DGX Spark is
officially supported). The Mac has none, and a CPU NeMo build would be
slower than Sortformer v1 already is. So:

```
Mac app (Swift) → shared/diarize_nemotron.py (stdlib urllib, no deps)
                → HTTP POST /diarize over Tailscale
                → ember: ~/nemo-diar/sidecar.py (FastAPI, port 8890)
                → NeMo Speech from source, GB10 GPU
```

Audio never leaves the tailnet. Nothing installs on the Mac for this
backend (registry `remote_service: True`).

## Ember side (already deployed 2026-10-04)

- venv: `~/nemo-diar/.venv` (Python 3.12, NeMo Speech 3.1.0 **editable
  from source** — PyPI 3.0.0 rejects the model's RoPE encoder).
- `lhotse==2.0.0a6` pinned (plain lhotse 1.33 → `lhotse.indexing`
  ImportError).
- **`TORCHDYNAMO_DISABLE=1` required** (eager flex_attention asserts in
  the dynamo path). Revisit for throughput once real meetings run.
- Model cache `~/nemo-diar/hf-cache` (`HF_HOME`).
- Auth: requests need header `X-Auth-Token` matching `~/.nemo-diar-token`
  on ember. One file to copy to the Mac.
- Service: `systemctl --user {status|restart} nemo-diar-sidecar`
  (unit enabled at boot, MemoryMax=8G so it can't starve vLLM).
- Health: `curl http://ember.tail17bf47.ts.net:8890/health` (no token).

## Mac side

- `shared/diarize_nemotron.py` — client; config in config.toml:
  `[nemotron] endpoint`, `auth_token_file`, `profile`
  (offline|low|very_low|ultra_low — offline is the accuracy profile,
  keep it for meetings).
- Models page: the row is **hardware-gated** — greyed until "Do you
  have an NVIDIA GPU host?" is toggled on (`models.py
  set-hardware-gate has_nvidia_host true`), then a "Check connection"
  button (`models.py remote-check diarize_nemotron`). Selecting it
  while the sidecar is down is refused by `set-active` with the reason.
- Eval: `python shared/diarisation_eval.py --backend nemotron ...`
  works with the frozen reviewed corpus, exactly like the other
  backends. Post-processing (voice-library naming etc.) is shared with
  the sortformer/pyannote backends, so an A/B measures the diarizer.

## Verified 2026-10-04

- sidecar: 401 without token, 200 with; low-prob guard returns no turns
  below 0.2 max-prob (synthetic sine voices correctly produce nothing);
  3-voice TTS meeting → 3 speakers, labels correct, global max prob 1.0.
- client → dispatch → post-processing chain runs clean
  (`_resolve_speaker_names`, segment-level/word-level assignment,
  prune-empty, split-long).
- `models.py` status/set-active/remote-check/set-hardware-gate
  CLI-level tested with a throwaway HOME.
- Eval harness end-to-end on a frozen 42s 3-speaker mini-corpus:
  count exact, confusion 0.147 (synthetic voices — directional only).

## Known gaps / next

- TitaNet `onnxruntime` is absent in the test venv, so speaker
  *naming* fell back to generic labels here. On the Mac the normal
  environment has it — re-verify naming works via this backend.
- Windows port not done (PyQt6 Models page needs the same gate UI).
- `torch.compile` throughput pass never done (dynamo is disabled).
- >8 speakers still caps at 8 channels (pyannote remains the only
  no-ceiling option).
- Eval with the real 16-meeting corpus: run
  `~/HiDock-eval/collect.sh` on the Mac when next at it, then
  `--backend nemotron --sample 0`.
