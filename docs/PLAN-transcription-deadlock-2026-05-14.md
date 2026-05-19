# Transcription queue stuck — root cause: Pipe deadlock in `refreshTranscriptionState`
Date: 2026-05-14
Branch: feature/voice-training
Source: live process inspection + `~/Library/Logs/hidock-menubar.log` (UTC stamps)

## Symptom (user report)
"Transcription is not working or completing."

## Evidence

### From the menubar log
- Auto-transcribe enqueues recordings on launch:
  - `17:24:09 Auto-transcribe: 11 recording(s) to process (0 fresh + 11 backlog, deduped)`
  - `17:24:09 Enqueued 11 recording(s), queue size: 11`
  - `17:24:09 runTranscription: transcribe …Rec89.mp3 --diarize --summarize`
- After that single `runTranscription:` line, **no progress, no completion, no error** is logged for the next ~17 min. App is then stopped and relaunched.
- Same pattern repeats across multiple sessions today (16:10, 16:17, 16:56, 17:22, 17:24, 19:27). Last one ends with `19:45:27 Transcription cancelled by user`.
- Rec89.mp3 has been re-enqueued **5 times** and never finished — the same backlog gets re-added on each launch because nothing ever marks a file complete.

### From live process state
- One Python process is alive: `PID 1170 — transcribe.py status` (no other transcribe.py running; no whisper/parakeet/diarize).
- Parent PID 993 = `hidock-mic-trigger` (the menubar app).
- Launched 13:42 local — has been running > 2 hours.
- `sample 1170` shows it parked here on the main thread:
  ```
  builtin_print → PyFile_WriteObject → _io_TextIOWrapper_write
      → _textiowrapper_writeflush → _io_BufferedWriter_write
          → _bufferedwriter_raw_write
  ```
  i.e. blocked on `write()` to stdout.
- `lsof -p 1170` confirms stdout (fd 1) is a `PIPE` to the parent app.
- `~/HiDock/transcription-pipeline/state.json` is 140 KB. That's the JSON `cmd_status` pretty-prints to stdout. macOS default pipe buffer is ~16 KB (grows to 64 KB under load). The child fills the pipe and blocks.

## Root cause — `refreshTranscriptionState` (AppDelegate.swift:6387–6430)

```swift
let pipe = Pipe()
process.standardOutput = pipe
let errPipe = Pipe()
process.standardError = errPipe

do {
    try process.run()
    process.waitUntilExit()                     // ← (1) waits with no reader
} catch { … }

let data = pipe.fileHandleForReading.readDataToEndOfFile()   // ← (2) too late
```

Classic Foundation `Process` deadlock. The stdout pipe is **only drained after `waitUntilExit()` returns**, but the child can't exit until it finishes writing, and it can't finish writing until the pipe is drained. With 140 KB of JSON vs a ~16–64 KB OS pipe buffer, the child blocks at the first `write()` past the buffer. Parent and child sit on each other forever.

## Why this breaks the *whole* transcription queue

`transcriptionDispatchQueue` is declared **serial** (line 68):

```swift
private let transcriptionDispatchQueue = DispatchQueue(label: "hidock.transcription", qos: .background)
```

and is used by **both**:
- `runTranscription` (line 5739) — the actual transcribe subprocess
- `refreshTranscriptionState` (line 6394) — the `transcribe.py status` poll

Once `refreshTranscriptionState`'s async block is wedged on `waitUntilExit()`, every subsequent `transcriptionDispatchQueue.async { … }` from `runTranscription` is enqueued but never runs. That matches the log exactly: the `log("runTranscription: …")` line (5738) prints because it runs *before* the `.async` enqueue; nothing after that ever executes.

## Trigger sequence on each launch
1. App launches, paints from cache, calls `refreshTranscriptionState` ("refreshing transcription state" log line at 17:23:47).
2. Subprocess `transcribe.py status` starts, prints 140 KB JSON, blocks on a full pipe.
3. Parent blocks on `waitUntilExit()`; the serial queue is now wedged.
4. Auto-transcribe later enqueues backlog → `runTranscription:` logs but the async block never runs.
5. Nothing completes. App eventually quits/relaunches and the cycle repeats.

## Fix (proposed)

Drain stdout (and stderr) **concurrently with the child**, the same pattern `runTranscription` already uses (AppDelegate.swift:5762–5772):

```swift
var outData = Data()
let outQueue = DispatchQueue(label: "hidock.refreshTranscriptionState.stdout")
pipe.fileHandleForReading.readabilityHandler = { handle in
    let chunk = handle.availableData
    if !chunk.isEmpty { outQueue.sync { outData.append(chunk) } }
}
var errData = Data()
let errQueue = DispatchQueue(label: "hidock.refreshTranscriptionState.stderr")
errPipe.fileHandleForReading.readabilityHandler = { handle in
    let chunk = handle.availableData
    if !chunk.isEmpty { errQueue.sync { errData.append(chunk) } }
}

try process.run()
process.waitUntilExit()

pipe.fileHandleForReading.readabilityHandler = nil
errPipe.fileHandleForReading.readabilityHandler = nil

// Pick up anything left after the handler was detached
let tailOut = pipe.fileHandleForReading.readDataToEndOfFile()
if !tailOut.isEmpty { outQueue.sync { outData.append(tailOut) } }
let tailErr = errPipe.fileHandleForReading.readDataToEndOfFile()
if !tailErr.isEmpty { errQueue.sync { errData.append(tailErr) } }

let data = outQueue.sync { outData }
```

Also worth adding (defence in depth):
- A wall-clock timeout around `refreshTranscriptionState` (e.g. 30 s) — currently there is none, so any future hang in this path will wedge the queue again.
- Consider giving `refreshTranscriptionState` its **own** serial queue (or a concurrent queue) so a status hang can't block actual transcriptions even if it recurs.

## Recovery (right now, no rebuild)
1. `kill 1170` (the stuck `transcribe.py status` PID — verify with `pgrep -lf "transcribe.py status"` first).
2. That should unwedge `transcriptionDispatchQueue` — pending `runTranscription` async blocks will start firing in order. Watch the menubar log for `runTranscription:` lines followed by `FILE_DONE` / completion entries.
3. If the queue doesn't drain after the kill, restart the app — backlog will be re-enqueued on launch and process normally **as long as `refreshTranscriptionState` doesn't deadlock first**. The deadlock recurs on every cold start with state.json larger than the pipe buffer, so this is a temporary mitigation only.

## Side observations
- `transcription-pipeline/config.json` has a Hugging Face token committed in plaintext. Rotate it and gitignore the file.
- Rec89.mp3 enqueued 5 times across the day — symptom of the deadlock, not a separate bug.
