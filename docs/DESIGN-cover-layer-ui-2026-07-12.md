# Cover-Layer UI — The Assistant Surface & Responsible-AI Control Plane

Research date: 2026-07-12
Sources: AWS Responsible AI core dimensions (aws.amazon.com/blogs/machine-learning/considerations-for-addressing-the-core-dimensions-of-responsible-ai-for-amazon-bedrock-applications, AWS Well-Architected Generative AI Lens), every.to "Introducing Cora" (ambient brief pattern), github.com/EveryInc/proof-sdk (provenance-on-every-edit), Smashing Magazine "Designing For Agentic AI" (Feb 2026), agent-UX pattern surveys 2025–26 (activity feeds, plan-then-act, risk-tiered approval)
Companion docs: runtime + contracts in `DESIGN-agentic-capabilities-2026-07-12.md`; overview in `DESIGN-agentic-overview-2026-07-12.md`

## Concept

A second face for the app: flip one radio control and the classic UI (tables, tabs, device strips) is *covered* by a single calm column of natural language — what happened, what the agents did, what they suggest, and one place to type what you want. Flip back and nothing is lost: both faces render the same underlying files.

This is deliberately **not a chat app**. The 2025–26 agent-UX consensus (and Cora in practice) is that chat is the wrong surface for stateful systems — users want outcomes surfaced, with conversation as the fallback, not the frame. The layer behaves like a competent chief-of-staff: it narrates, proposes, and asks for decisions; it does not wait to be prompted.

The same surface doubles as the app's **responsible-AI control plane**: every one of AWS's 8 dimensions is a *rendered feature* here, not a policy claim.

## Toggle & platform implementation

**macOS** — overlay inside the existing window (not a separate `NSWindow`; the view model and all closures already live here):

```swift
// MainWindowView.body (today: plain HStack { mainColumn; terminal pane })
ZStack {
    HStack(spacing: 0) { mainColumn /* + terminal pane */ }
        .disabled(viewModel.coverLayerVisible)
    if viewModel.coverLayerVisible {
        CoverLayerView(viewModel: viewModel)
            .background(.regularMaterial)   // classic UI ghosted beneath — the literal "cover"
            .transition(.opacity)
    }
}
```

- Toggle: a two-segment radio control — **⦿ Classic ⦾ Assistant** — in the existing footer strip; choice persisted via config (`[cover_layer] default_on`).
- New files: `Sources/Views/CoverLayerView.swift`, `Sources/Views/CoverLayerCards.swift`, `Sources/AgentActivityStore.swift` (`ObservableObject` with `DispatchSource` file watchers on `activity.jsonl` + `pending/`).
- `HiDockViewModel` additions: `@Published var coverLayerVisible`, `agentActivity`, closures `onApproveAction` / `onDenyAction` / `onSubmitIntent` (implemented in `AppDelegate` via a `runAgentsCLI` helper cloned from `runTranscription(arguments:)`).

**Windows** — `QStackedWidget` page swap (translucent overlays fight native table repaint in Qt): page 0 = existing central layout, page 1 = `CoverLayerWidget`. Radio pair in the header strip; `Windows-App/ui/cover_layer.py` + `Windows-App/core/agents.py` (QFileSystemWatcher + worker-thread CLI bridge, same pattern as `core/summarize.py`).

Parity is structural: both UIs are dumb renderers of the same two file contracts.

## Layout — one centered column (~680 pt max), four zones, no chrome

```
┌────────────────────────────────────────────────┐
│  ⏸ Pause agents      ● local · ○ cloud   Audit │   ① control strip
│────────────────────────────────────────────────│
│  ▌Morning brief — 3 things need you            │   ② the Brief (when unread)
│  ▌2 commitments went stale · 1 decision        │
│  ▌conflicts with 12 Jun · draft ready for Sam  │
│                                                │
│  ┌──────────────────────────────────────────┐  │   ③ approval cards
│  │ Follow-up to Sam re: Q3 budget    AI ✦   │  │
│  │ Recaps 2 decisions, 3 action items       │  │
│  │ [Approve] [Edit] [Deny — what instead?]  │  │
│  └──────────────────────────────────────────┘  │
│                                                │
│  09:14  Transcribed "Q3 planning" (42 min)     │   ④ activity feed
│         whisper-turbo · local ✦ why?           │
│  09:16  Extracted 4 action items   claude ☁    │
│  09:16  Checked 3 decisions against history    │
│                                                │
│────────────────────────────────────────────────│
│  › Ask, steer, or search…                      │   ⑤ input line
└────────────────────────────────────────────────┘
```

1. **Control strip** — pause/kill switch (suspends `tick`, terminates running agent subprocesses), live local/cloud indicator (derived from each event's `provenance.locality`), and an Audit link (filtered view of the `event_log` SQLite trail).
2. **The Brief** — when an unread briefing exists it *is* the home screen (Cora's pattern): a scannable digest with "N things need you" up top. Read it and the layer collapses to feed + cards.
3. **Approval cards** — pending actions rendered by `kind`: title, one-line natural-language rationale, **Approve / Edit / Deny**. Edit opens the payload for overwrite before approval (drafts are expected to need human rewriting — user decision). Deny reveals the *"what should happen instead?"* field; the answer is written to Lessons, so every denial permanently improves the system.
4. **Activity feed** — reverse-chronological natural-language rows from `activity.jsonl`, including `pipeline` events from the existing transcribe/summarise flows, so the layer narrates *everything* the app does. Each row: provenance chip (engine/model + `AI ✦` label), locality badge, and an expandable **why?** revealing the run's `tool_use` steps ("Searched meetings for 'Q3 budget' → 3 matches").
5. **Input line** — plain language in, three outcomes:
   - **Deterministic intent** (<100 ms, no LLM): sync, transcribe, summarise, search, settings — `shared/agents/intents.py` maps verbs to the same code paths the classic buttons call, then logs an `intent_executed` event ("Started sync for HiDock H1").
   - **Agent fallback**: anything else becomes a `query_agent` run over the meetings MCP, streamed into the feed token-by-token (`stream-json` deltas — plumbing already exists in `llm_cli.query_streaming`).
   - **Multi-step requests** get a **plan-then-act card**: the proposed step list shown *before* execution, editable, then run with each step narrated in the feed.

## The 8 AWS Responsible-AI dimensions — rendered, not claimed

| Dimension | Rendered as |
|---|---|
| **Controllability** | Pause/kill switch · autonomy tiers per agent · `--allowedTools` scoping · per-agent on/off in settings |
| **Transparency** | `AI ✦` label on every generated artifact · provenance chip (engine, model, timestamp) · the layer itself announces what ran and why |
| **Explainability** | *why?* expander on every feed row (tool steps) · every extracted item links back to the transcript span it came from |
| **Privacy & Security** | local/cloud badge on every event · M365 off by default, opt-in, visibly badged when active · all state in `~/HiDock/`, nothing leaves the machine un-narrated |
| **Governance** | SQLite `event_log` audit trail (Audit link) · lessons carry `sources:` action ids · model adoptions recorded with eval numbers |
| **Veracity & Robustness** | confidence scores on transcript/attribution rows · citations (`refs`) on agent claims · model changes gated by evals on the user's own golden set |
| **Safety** | propose-by-default autonomy · gated writes (email/calendar drafts never auto-send) · whitelisted auto-apply kinds only, always reversible |
| **Fairness** | speaker-attribution confidence surfaced per segment and correctable in place (ties into the in-flight tagging loop) — misattribution is visible, not silent |

## Compound principles in the UI itself

- Every approve / edit / deny is a lesson write — the review step *is* the training signal.
- The layer periodically shows **"What I learned from you this week"** — lessons as visible, editable memory (memory surfacing pattern), tap to correct or delete.
- Plan-then-act cards mirror the plan→work→review→compound loop at interaction scale: the user reviews the plan (cheap) instead of the execution (expensive).

## Experimental / bleeding-edge (design intentions, held loosely)

- **Briefing-first, not app-first**: when the layer is on and a brief is unread, the brief is the entire opening screen. Software that starts with "here's what matters" instead of "here are my controls".
- **Generative UI within allow-listed kinds**: agents choose *which* card kinds to emit and how to compose them (a prep card may embed a mini relationship timeline), but only from the typed component set — expressiveness without unbounded rendering.
- **LED ticker as ambient status**: the in-flight dot-matrix ticker doubles as the layer's ambient channel — "brief ready", "2 approvals waiting" scroll across it while the user works elsewhere. The heatmap/LED surface becomes the agent's presence indicator.
- **No menus, no tabs, no chrome inside the layer** — if something needs a menu, it belongs to the classic face; the radio toggle is always one flip away.
- **Quiet by design**: the layer never notifies for things that can wait for the next brief; per-agent action budgets keep card volume low enough that every card deserves attention.

## Phasing

- **PR 2** — cover layer v1: read-only feed + radio toggle + control strip, both platforms; existing pipeline flows instrumented as `pipeline` events. (Immediately useful with zero agents: a live narration of the app.)
- **PR 3** — approval cards + deny-reason → lessons.
- **PR 4** — input line + intent router + streamed fallback agent + plan-then-act cards.
- **PRs 7/8** — `adopt_model` cards and the Brief zone arrive with their agents.

## Planned

- [ ] PR 2 feed + toggle (first visible slice of the whole programme)
- [ ] PR 3 cards
- [ ] PR 4 input + intents
- [ ] Brief zone + LED ambient channel (with PR 8)

## Rejected / Not Applicable

- **Separate assistant window/panel** — rejected on macOS; overlay keeps one window, one view model, and makes the "cover" literal.
- **Chat-first layout** — rejected; input is the fifth zone, not the frame.
- **Translucent overlay on Windows** — rejected; Qt native-table repaint fights translucency, `QStackedWidget` swap is clean and equivalent.
- **Unconstrained generative UI** — rejected; card kinds are the allow-list.
