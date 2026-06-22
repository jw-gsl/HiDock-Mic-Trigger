# Status colour review & overhaul

Date: 2026-06-22
Trigger: James — "review the colours of the different statuses, feel like there
could be an overhaul."

## Current palette (StatusBadge.color / SyncHeaderSection.statusColor)
| State | Colour | Issue |
|---|---|---|
| On device | secondary grey | ok |
| Downloaded (success) | green | ok |
| Transcribed | purple | purple vs indigo (summarised) too close |
| Summarised | indigo | " |
| Imported (info) | blue | same blue as Merged → indistinguishable |
| Merged | blue (.info) | same as Imported |
| Skipped | teal 0.6 | ok-ish |
| Removed | red 0.6 | muted red easily read as an error (Failed is red) |
| Failed (error) | red | ok |
| Warning / needs-tagging | orange | ok |

Problems: (1) transcribed/summarised hues nearly identical; (2) Imported and
Merged share blue; (3) Removed's muted red collides semantically with Failed's
red; (4) no single clear "progression ramp".

## Proposed palette
Principle: a cool **progression ramp** for the pipeline (each step "deeper"),
distinct hues for source/structural states, warm/earthy tones for user-action
states, and **red reserved strictly for failure**.

| State | New colour | Rationale |
|---|---|---|
| On device | grey (secondary) | inert — not local yet |
| Downloaded | **green** | "I have the file" — baseline win |
| Transcribed | **teal** | text ready — one step past green |
| Summarised | **indigo** | AI-distilled — deepest pipeline state |
| Imported | **blue** | external source marker |
| Merged | **purple** | structural combination — distinct from Imported |
| Skipped | **brown** | parked / set aside on purpose (earthy, low-key) |
| Removed | **pink** | deliberate destructive — distinct from error red |
| Failed | **red** | errors only |
| Needs tagging / warning | **orange** | attention |

Ramp reads green → teal → indigo as a recording advances; blue/purple sit apart
for source/structural; brown/pink/orange/red are clearly non-pipeline. All carry
text labels too, so colour is reinforcing, not the sole signal.

## Implementation
- Add a `.merged` case to `StatusLevel`; the merge-parent badge uses it instead
  of `.info` so Merged ≠ Imported.
- Update the two colour maps (`StatusBadge.color`, `SyncHeaderSection.statusColor`).
- Shipping it so it can be judged on screen — any individual hue is a one-line
  tweak afterwards.
