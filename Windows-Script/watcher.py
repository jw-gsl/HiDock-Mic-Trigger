#!/usr/bin/env python3
"""HiDock background watcher — monitors for completed recordings and downloads them.

Designed to run persistently on a Windows machine where the HiDock is always
docked.  Avoids interfering with live recordings by:

1. Polling infrequently (default every 5 minutes).
2. Taking two file-list snapshots separated by a stabilisation delay.
3. Only downloading files whose size is identical across both snapshots,
   meaning the recording has finished writing.
4. Backing off if any USB communication fails (device may be busy recording).

Logs to both the console and a rotating log file so issues are easy to trace
after the fact.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Import everything we need from the extractor module in the same directory.
from extractor import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_STATE_PATH,
    HiDockProtocolError,
    download_one,
    find_device,
    load_config,
    load_state,
    output_path_for,
    prepare_device,
    query_file_list,
    release_device,
    resolved_output_dir,
    save_state,
)

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "watcher.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("hidock-watcher")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Rotating file — 5 MB per file, keep 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def snapshot_file_list(timeout_ms: int = 3000) -> list[dict] | None:
    """Return the current file list from the device, or None on failure."""
    try:
        dev = find_device()
    except FileNotFoundError:
        return None

    try:
        interface_number = prepare_device(dev)
    except Exception:
        return None

    try:
        return query_file_list(dev, request_id=2, timeout_ms=timeout_ms)
    except Exception:
        return None
    finally:
        release_device(dev, interface_number)


def stable_files(
    snapshot_a: list[dict],
    snapshot_b: list[dict],
) -> list[dict]:
    """Return files whose name and size match across both snapshots.

    A file whose size changed between the two snapshots is likely still being
    recorded and should NOT be downloaded yet.
    """
    b_by_name: dict[str, int] = {f["name"]: f["length"] for f in snapshot_b}
    result: list[dict] = []
    for f in snapshot_a:
        name = f["name"]
        if name in b_by_name and b_by_name[name] == f["length"]:
            result.append(f)
    return result


def files_needing_download(
    recordings: list[dict],
    state: dict,
    output_dir: Path,
) -> list[dict]:
    """Filter to recordings not yet successfully downloaded."""
    downloads = state.get("downloads", {})
    pending: list[dict] = []
    for rec in recordings:
        name = rec["name"]
        stored = downloads.get(name, {})
        if stored.get("downloaded"):
            out_path = Path(stored.get("output_path", output_path_for(name, output_dir)))
            if out_path.exists():
                continue
        pending.append(rec)
    return pending


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_loop(
    poll_interval: int = 300,
    stabilise_delay: int = 30,
    timeout_ms: int = 5000,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    verbose: bool = False,
) -> None:
    log = setup_logging(verbose=verbose)
    log.info("HiDock watcher started")
    log.info("  Poll interval:     %d seconds", poll_interval)
    log.info("  Stabilise delay:   %d seconds", stabilise_delay)
    log.info("  Config:            %s", config_path)
    log.info("  State:             %s", state_path)

    config = load_config(config_path)
    output_dir = resolved_output_dir(config)
    log.info("  Output directory:  %s", output_dir)

    consecutive_failures = 0
    max_backoff = poll_interval * 4

    while True:
        try:
            _poll_once(
                log=log,
                stabilise_delay=stabilise_delay,
                timeout_ms=timeout_ms,
                config_path=config_path,
                state_path=state_path,
            )
            consecutive_failures = 0
        except KeyboardInterrupt:
            log.info("Interrupted — shutting down.")
            break
        except Exception as exc:
            consecutive_failures += 1
            backoff = min(poll_interval * (2 ** consecutive_failures), max_backoff)
            log.warning(
                "Poll failed (%s). Backing off %d seconds. (consecutive failures: %d)",
                exc,
                backoff,
                consecutive_failures,
            )
            time.sleep(backoff)
            continue

        time.sleep(poll_interval)


def _poll_once(
    log: logging.Logger,
    stabilise_delay: int,
    timeout_ms: int,
    config_path: Path,
    state_path: Path,
) -> None:
    config = load_config(config_path)
    output_dir = resolved_output_dir(config)
    state = load_state(state_path)

    # --- First snapshot ---
    log.debug("Taking first file-list snapshot...")
    snap_a = snapshot_file_list(timeout_ms=min(timeout_ms, 3000))
    if snap_a is None:
        log.info("Device not reachable — skipping this cycle.")
        return

    if not snap_a:
        log.debug("Device connected but no recordings found.")
        return

    log.debug("Snapshot A: %d file(s)", len(snap_a))

    # --- Check if anything actually needs downloading before stabilising ---
    pending_a = files_needing_download(snap_a, state, output_dir)
    if not pending_a:
        log.debug("All %d recording(s) already downloaded — nothing to do.", len(snap_a))
        return

    log.info(
        "%d new recording(s) detected. Waiting %d seconds to confirm they are stable...",
        len(pending_a),
        stabilise_delay,
    )

    # --- Wait, then take second snapshot ---
    time.sleep(stabilise_delay)

    snap_b = snapshot_file_list(timeout_ms=min(timeout_ms, 3000))
    if snap_b is None:
        log.warning("Device disappeared during stabilise wait — skipping this cycle.")
        return

    # --- Only download files whose size hasn't changed ---
    ready = stable_files(pending_a, snap_b)
    still_recording = len(pending_a) - len(ready)

    if still_recording > 0:
        log.info(
            "%d file(s) still changing size (likely recording) — skipping those.",
            still_recording,
        )

    if not ready:
        log.info("No stable files ready for download this cycle.")
        return

    # --- Download each stable file ---
    for rec in ready:
        name = rec["name"]
        length = rec["length"]
        log.info("Downloading %s (%d bytes)...", name, length)
        try:
            result = download_one(
                name,
                length=length,
                output_dir=output_dir,
                timeout_ms=timeout_ms,
                config_path=config_path,
                state_path=state_path,
            )
            if result["downloaded"]:
                log.info("  OK  -> %s", result["outputPath"])
            else:
                log.warning(
                    "  Partial: wrote %d / %d bytes -> %s",
                    result["written"],
                    result["expectedLength"],
                    result["outputPath"],
                )
        except Exception as exc:
            log.error("  FAILED: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Background watcher that downloads new HiDock recordings automatically."
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=300,
        help="Seconds between polls (default: 300 = 5 minutes)",
    )
    parser.add_argument(
        "--stabilise-delay",
        type=int,
        default=30,
        help="Seconds to wait between file-list snapshots to confirm a recording "
        "has stopped growing (default: 30)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=5000,
        help="USB read/write timeout in milliseconds (default: 5000)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug-level logging",
    )
    args = parser.parse_args()

    run_loop(
        poll_interval=args.poll_interval,
        stabilise_delay=args.stabilise_delay,
        timeout_ms=args.timeout_ms,
        verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
