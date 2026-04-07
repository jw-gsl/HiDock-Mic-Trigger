# Shared Modules

Cross-platform Python modules used by both the macOS and Windows apps. These provide the intelligence layer on top of Whisper transcription.

## Modules

| Module | Purpose | Key functions/classes |
|--------|---------|---------------------|
| `transcript_writer.py` | Write Markdown transcripts with YAML frontmatter | `write_transcript()`, `parse_frontmatter()`, `auto_title()`, `build_frontmatter()` |
| `llm_cli.py` | Detect and invoke LLM CLI tools (claude, codex, gemini, ollama) | `detect_engines()`, `get_engine()`, `query()`, `query_json()` |
| `summarize.py` | LLM-powered transcript summarization | `summarize(transcript_text) -> dict` |
| `knowledge.py` | SQLite knowledge graph over transcript frontmatter | `KnowledgeGraph` class — `rebuild()`, `search()`, `get_person_profile()`, `list_action_items()` |
| `config_store.py` | Cross-platform TOML configuration | `ConfigStore` class, `get_config()` singleton |
| `obsidian.py` | Obsidian vault sync with wikilinks | `VaultSync` class — `sync_transcript()`, `sync_all()`, `generate_person_notes()` |
| `hooks.py` | Post-transcription shell hooks + Obsidian sync | `run_hooks_pipeline()`, `run_post_transcription_hook()` |
| `migrate.py` | Add frontmatter to existing transcripts | `migrate()`, `add_frontmatter_to_file()` |
| `audio_utils.py` | Audio loading, MFCC/embedding extraction | `load_audio()`, `extract_mfcc()`, `extract_embedding()` |
| `diarize_lite.py` | Lightweight speaker diarization | `diarize()` |
| `voice_library_lite.py` | Speaker enrollment and identification | `VoiceLibrary` class |
| `models.py` | Whisper model registry and management | Model download, status, path resolution |

## Architecture

All modules are designed for **graceful degradation**:
- No LLM CLI installed? Summarization is skipped, transcription still works.
- No Obsidian vault configured? Sync is disabled by default.
- No config file? Sensible defaults are used.
- Every failure in the intelligence layer is non-fatal.

The Markdown files with YAML frontmatter are the **source of truth**. The SQLite knowledge graph is a **rebuildable cache** — it can be deleted and rebuilt at any time with `python -m shared.knowledge rebuild`.

## Import pattern

Each platform entry point adds the repo root to `sys.path`:

```python
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
```

Then imports shared modules lazily inside functions (not at module level) to avoid blocking startup if optional dependencies are missing:

```python
def transcribe_file(mp3_path, summarize=False):
    from shared.transcript_writer import write_transcript
    # ...
    if summarize:
        from shared.summarize import summarize as run_summarize
```

## CLI tools

Several modules have CLI interfaces:

```bash
# Knowledge graph
python -m shared.knowledge rebuild
python -m shared.knowledge search "budget review"
python -m shared.knowledge person "Sarah"
python -m shared.knowledge actions --status open
python -m shared.knowledge stats
python -m shared.knowledge people

# Migration
python -m shared.migrate                    # dry-run
python -m shared.migrate --apply            # apply changes
python -m shared.migrate --apply --rebuild-index
```

## Configuration

All modules read from `shared/config_store.py` which provides a unified TOML config:

| Platform | Config path |
|----------|-------------|
| macOS/Linux | `~/.config/hidock/config.toml` |
| Windows | `%APPDATA%\HiDock\config.toml` |

See `config_store.py` for all available settings and defaults.

## Tests

128 tests across 9 test files:

```bash
python -m pytest shared/tests/test_transcript_writer.py \
  shared/tests/test_llm_cli.py shared/tests/test_summarize.py \
  shared/tests/test_knowledge.py shared/tests/test_config_store.py \
  shared/tests/test_migrate.py shared/tests/test_obsidian.py \
  shared/tests/test_hooks.py mcp-server/tests/test_server.py -v
```

Tests require only `pytest`, `numpy`, and `scipy` — no heavy ML dependencies.

## Dependencies

The intelligence modules have **zero external Python dependencies** (no pip installs required). They use only the standard library plus `numpy`/`scipy` which are already present in the transcription venvs. The TOML parser, YAML frontmatter parser, and LLM CLI integration are all custom implementations to avoid adding dependencies.
