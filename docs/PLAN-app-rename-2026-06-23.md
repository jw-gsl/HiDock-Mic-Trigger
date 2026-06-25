# App rename — naming research & brainstorm

Research date: 2026-06-23
Status: research / ideas (no decision yet)
Sources: web search (US), June 2026 — see "Clash-check sources" at the end.

> ⚠️ **This is a web-based clash check, not a legal trademark clearance.** It tells
> us which names already have a *visible product* in our category. Before
> committing to any name, run a proper USPTO/EUIPO search (Class 9 software +
> Class 42 SaaS) and confirm domain/app-store availability. Treat the verdicts
> below as "safe enough to shortlist" vs "don't bother".

## Why rename

The project is still called **HiDock Tools** / **HiDock Mic Trigger**, but it has
drifted well past that:
- It ingests recordings from **HiDock USB docks, generic USB recorders/SD cards,
  *and* Plaud cloud accounts** — it's device-agnostic, not a HiDock accessory.
- It transcribes **fully locally/offline** (Whisper, Parakeet, Sortformer) — "all
  local, no network, no API keys".
- It produces **structured meeting intelligence** — summaries, a knowledge graph,
  Obsidian sync, and an MCP server that exposes meeting knowledge to AI agents.

So the name is now both inaccurate (it's not "HiDock"-specific) and undersells it
(it's a meeting-memory hub, not a "mic trigger"). The user's framing: *"more
meeting brain / over-arching transcription hub."*

## What it actually is (the naming brief)

Three identity pillars a name should evoke at least one of:
1. **Multi-source hub** — many devices/recordings flow into one place.
2. **Local & private** — on-device, offline, your data stays yours.
3. **Meeting memory / intelligence** — capture → transcript → searchable knowledge.

Constraints:
- Cross-platform (macOS primary, Windows port) — nothing OS-specific.
- Must **not** sound like the incumbents (see below) or collide with an existing
  product in transcription / meeting-notes / voice / notes / AI-memory.
- Brandable: short-ish, easy to say, `.app`/`.com` obtainable (likely with a
  modifier), clean app-store search.
- Not locked to "HiDock" or "Plaud" (it spans both and more).

## The crowded landscape (what we're naming *against*)

This category is dense — a name must stand clear of all of these:
Otter, Fireflies, Fathom, Granola, tl;dv, Krisp, Notta, Tactiq, Voicenotes,
VOMO, Limitless (Pendant), Plaud, Superwhisper/MacWhisper/Aiko, Recall.ai,
Rewind, **Murmur** (private-by-default macOS transcription — closest match to our
positioning), **Steno** (several macOS voice-to-text apps), Mem, Reflect, Granola.

## Recommendations (tiered)

### Tier 1 — Recommended (clear in our category)

| Name | Why it fits | Clash status |
|---|---|---|
| **Tributary** | Many streams (devices/recordings) flow into one body — the multi-source hub, exactly. Calm, distinctive, not "AI-cute". | No transcription/meeting/notes product found. ✅ Clearest of the lot. |
| **Catchment** | The area where many streams collect — same hub metaphor, a bit more technical/British. Pairs with Tributary. | No software product found in-category. ✅ |
| **Baleen** | A whale's baleen *filters everything* out of the water it takes in — ingest-from-anywhere + listening. On the whale theme you like, and actually clear (unlike Orca). | B2B namesakes only (Baleen Solutions/fintech, Baleen Filters, Baleen Data) — **none in audio/meeting/notes software**. ✅ in-category; `.com` taken, use `baleen.app`. |
| **Minke** | A whale species — short, soft, brandable, and genuinely unused in software. The "clear" way to do the whale idea. | No app/software found at all. ✅ Cleanest whale option. |
| **Soundings** | Nautical depth-measurement + "sound"/audio + whales "sounding" when they dive. Triple meaning; quietly clever. | No in-category product (a sailing magazine exists; different class). ✅ |

### Tier 2 — Usable, with minor friction

| Name | Why it fits | Watch-out |
|---|---|---|
| **Spool** | Tape-reel metaphor — recordings spooling in. Short, brandable. | A "Spool" media app exists (unrelated); generic-ish. |
| **Parley** | A conversation/negotiation — talk/meeting feel. | Common word; easily confused with betting "parlay". No direct app clash. |
| **Palaver** | A drawn-out talk/conference — characterful. | Slightly negative idiom ("a palaver" = a fuss); no app clash. |
| **Confab** | Informal "conference" — friendly. | A well-known design *conference* brand; no transcription app, but mildly muddy. |

### Tier 3 — The whale / pod theme (you asked for this)

You like **Orca** and pods of killer whales — it's a *great* metaphor:
**echolocation = turning sound into meaning** (literally transcription), a **pod =
your collection of devices/recordings**, and orcas are deep, social, and have
long memories. The problem is the literal words are taken:

- **Orca** ❌ — collides with **GNOME Orca** (a *screen reader* — speech/
  accessibility, the worst possible neighbour for an audio app), **OrcaSlicer**
  (huge 3D-printing tool), and **Orca Security** (cloud-security unicorn). Too
  confusing as a standalone brand.
- **Pod** ❌ — saturated by podcasting (Podnotes, Podsqueeze, etc.).
- **Narwhal** ❌ — "Narwhal for Reddit", Android Studio "Narwhal", Narwal vacuums.
- **Sonar / Echo / Fathom** ❌ — SonarQube, Amazon Echo, Fathom (a meeting
  notetaker) respectively.

**How to keep the whale idea and stay clear:**
- Use **Baleen**, **Minke**, or **Soundings** (Tier 1) — all on-theme and clear.
- Keep **"pod"** in the *product language*, not the name: collections of
  recordings = "pods", the tagline "your pod, on your machine", a pod-of-orcas
  app icon. You get the metaphor without fighting the podcast/Orca trademarks.
- If you love the orca sound specifically, a **coined compound** (e.g. *Orcabox*,
  *Orcadeck*) sidesteps the bare-word clashes — but it inherits Orca's baggage
  and is weaker than Minke/Baleen. Lower priority.

**Recommended whale pick: `Minke`** (cleanest) or **`Baleen`** (richest metaphor).

## Orca theme — deep dive (2026-06-23)

The orca metaphor fits this product better than almost any generic "AI notes"
name, because nearly every part of orca biology maps onto a real feature:

| Orca reality | What it maps to in the app |
|---|---|
| **Echolocation** — send out clicks, get meaning back | Audio in → transcript/insight out (the core loop) |
| **Pod** — a tight family that travels together | Your collection of devices/recordings in one place (the hub) |
| **Matriline** — pods led by the oldest female; decades of memory | The persistent knowledge base / long meeting memory |
| **Saddle patch** — the unique grey mark that IDs each individual | Speaker identification / diarization |
| Each pod has its own **dialect** of calls | Your org's own meetings, vocabulary, recurring topics |
| **Resident** orcas stay in home waters | Local-first, on-device, private |

### Clash-checked orca / cetacean vocabulary

**Tier A — ownable & on-theme (recommended):**
- **Saddlepatch** (or **Saddle**) — no software brand exists (just an artist
  handle / Etsy). The saddle patch is *the* orca-ID feature → perfect for speaker
  ID: *"every voice gets its saddle patch."* Distinctive, memorable, makes a
  great mark. **Best orca-native pick.**
- **Dorsal** — clear in-category (the medical scribe "Dorascribe" is a different
  word). The dorsal fin is the unmistakable orca silhouette → an excellent, simple
  logo. Short and brandable. *Caveat: common anatomical word; do a trademark pass.*
- **Minke** / **Baleen** — cetacean and clear in-category (from earlier rounds);
  softer than "killer whale" but on-theme.

**Tier B — good metaphor, some friction:**
- **Dialect** — lovely "every pod has its own dialect" angle, but it's a generic
  word *and* GNOME ships a translation app called Dialect. Moderate risk.
- **Flukeprint** — the glassy patch a whale leaves on the surface (used to track
  them) = the *trace* a meeting leaves behind. Very ownable, but obscure (needs
  explaining).
- **Spyhop** — orcas rise vertically to survey their surroundings (= review/
  observe), but "spy" surfaces surveillance/spyware in search → bad SEO and
  connotation neighbourhood.

**Tier C — taken (avoid):** Orca (GNOME screen reader / OrcaSlicer / Orca
Security), **Orcinus** (orca-sighting apps already use it), **Grampus** (game
studio + J-League club), **Keiko** (KeikoAI wellness app — and "private by
design", adjacent), **Matriarch/Matriline** (several apps), **Pod** (podcast-
saturated), **Narwhal** (Reddit client / vacuums).

- **Blackfish** — double-no. (1) Crowded in software: **Blackfish Software LLC**
  (IE Tab, 4M+ users), **Blackfish & Co.** (a dev/design + app-branding studio),
  a "Blackfish" app-distribution platform. (2) Connotation: though "blackfish" is
  an old word for orca, it now means the **2013 captivity documentary** (Tilikum,
  PTSD, the film that gutted SeaWorld) — captivity/cruelty associations you don't
  want on a meeting tool.

> ⚠️ **Cultural note:** avoid **Salish** (Salish Sea is the Southern Residents'
> home, but it's a Coast Salish Indigenous name — don't appropriate it as a brand).

### Keeping "pod" without owning the word
Name the app something ownable (**Saddlepatch** / **Dorsal**), and make **"pod"**
the in-product term for a saved group of recordings/devices — plus a pod-of-orcas
motif in the icon, onboarding, and copy. You get the metaphor without fighting the
podcast/Orca trademarks.

### Identity direction (if you go orca)
- **Logo:** a single orca **dorsal fin** (clean silhouette) *or* the **saddle-patch**
  crescent — both read instantly as orca without a cartoon whale.
- **Palette:** deep slate/ink + cold sea-cyan — "deep, local, quiet".
- **Taglines:** *"Every voice, surfaced."* · *"Your meetings, in one pod — on your
  machine."* · *"Sound in. Sense out."* (echolocation).

### Orca-native recommendation
**Saddlepatch** (most ownable, best metaphor, strong mark) or **Dorsal**
(cleanest + iconic logo). For a gentler cetacean route, **Minke**.

### Orca across cultures & languages (2026-06-23)

Verified words for orca (sources at the end):

| Word | Culture / language | Meaning / note |
|---|---|---|
| **Sgaana** (Skana, Ska'ana) | Haida | "supernatural being / power"; orcas = reincarnated ancestors, "Lord of the Ocean" |
| **Kéet** | Tlingit | killer whale; a **sacred clan crest** (at.óow) |
| **Maxʼinux̱** (Maxinuxw) | Kwakwaka'wakw (Kwak'wala) | "side-by-side tribe"; great chiefs become orcas |
| **Qw'e lh'ol' me chen** | Lummi / Coast Salish | "our relations who live under the water" |
| **Polossatik** | Alutiiq (Kodiak) | "the feared one" |
| **Shachi** (鯱) | Japanese | orca; root of **shachihoko**, the tiger-headed sea-guardian on castle roofs (e.g. Nagoya) |
| **Repun Kamuy** | Ainu | "master/god of the open sea" (orca deity) |
| **Aarluk** | Inuktitut | killer whale |
| **Kosatka / Kasatka** | Russian (касатка) | orca |
| **Orcus** | Latin / Roman myth | underworld god and **punisher of broken oaths** (keeper of what was said — apt, but dark) |

> 🛑 **Cultural-respect caveat — read first.** The Pacific-Northwest First-Nations
> names (**Sgaana, Kéet, Maxʼinux̱**, …) are not just "words for orca" — they're
> **sacred crests and beings** tied to specific living clans and cultures. Using
> one as a commercial brand would be appropriation, and several are owned crests.
> Treat these as *context for why the orca metaphor resonates*, **not** as names
> to take. (Also: **Skana** was a famous *captive* orca at the Vancouver Aquarium —
> a captivity association like Blackfish.)

**What's actually usable from this list:**
- **Shachi** (Japanese) — **the standout.** No software clash found; short,
  brandable, and it carries gorgeous imagery via **shachihoko** — the golden
  orca-guardian that sits on castle rooftops *protecting the building* (a guardian
  that watches over your meetings writes itself). "Shachi" is an everyday word and
  the shachihoko is already a widely-commercialised civic motif (Nagoya's mascot),
  so the cultural-appropriation risk is far lower than the sacred PNW crests —
  though still borrow respectfully. **Recommended culturally-derived pick.**
- **Aarluk** (Inuktitut) — distinctive and brandable; no app surfaced. *Verify a
  company-name/trademark check first (there may be a Canadian firm by this name),
  and it's still a borrowing — weigh that.*
- **Orcus** (Latin) — the "keeper of oaths / what was said" angle is genuinely on-
  theme for a meeting record, but it's dark (underworld), close to "Orca", and
  used in games (D&D demon prince) + as a dwarf-planet name. Mixed; lower priority.
- **Kosatka** (Russian) — neutral and brandable-ish, but it's just "the Russian
  for orca" with little resonance for an English audience, and **Kasatka** was
  also a SeaWorld orca. Meh.

**Recommendation from the cultural angle:** **Shachi** — and lean into the
*shachihoko guardian* identity (a stylised golden orca-fin watching over your
recordings). It gives you the orca soul of the brand without taking a sacred
First-Nations name.

### Arctic & Pacific orca names (2026-06-23)

(*"Eskimo" is now generally avoided — using Inuit / Iñupiaq / Yup'ik / Greenlandic.*)

**Arctic — the Inuit language family** (cognates across the Arctic):

| Word | Language | Note |
|---|---|---|
| **aarluk** | Inuktitut | killer whale |
| **aarlu** | Inuinnaqtun | killer whale |
| **aaxlu / aaġlu** | Iñupiaq (N. Alaska) | killer whale |
| **aalluk** | Labrador Inuttut | killer whale |
| **mesungesak** | Central Yup'ik | orca (hard to brand) |
| *aarlirijuk* | Inuktitut | "fear of killer whales" — a real word; lovely trivia, not a name |
| **Akhlut** | Inuit folklore | the **orca-wolf** that becomes a wolf on land — a shapeshifter of sea ↔ land |

**Pacific — Māori / Polynesian** (via Te Aka Māori Dictionary, Te Ara):

| Word | Note |
|---|---|
| **Maki** | Māori for orca — short, friendly, brandable |
| **Kākahi** | Māori for orca/whale; *chiefs were likened to kākahi* for intelligence & hunting (also a freshwater mussel) |
| **Keo** | another Māori term for orca |
| **Taniwha** | water-guardian spirit; orcas honoured as taniwha (broad sacred concept) |
| **ʻālaluka** | Hawaiian for killer whale — but a transliteration/loanword (orcas are rare in Hawai‘i), so not a deep traditional term |

> 🛑 **Same respect caveat.** These carry cultural weight too — Māori regard whales
> as *taonga* (treasure); **taniwha** is a sacred concept; and **aarluk** is the
> name of a real **majority-Inuit-owned** consulting firm (Aarluk Consulting,
> Iqaluit — now amalgamated/inactive, but the heritage is real). Less "owned
> crest" than the Haida/Tlingit names, but still a borrowing — use respectfully,
> and ideally consult if you pursue one seriously.

**Standouts from this round:**
- **Maki** (Māori, orca) — **the new contender.** Short, warm, easy to say/spell
  ("MAH-kee"), no transcription/meeting app clash. Carries the orca soul without a
  sacred-crest problem. The only friction is benign cross-meanings (sushi roll;
  a lemur) — which actually make it *sticky* and memorable. `maki.app` worth a
  check. Strong, and gentler than "killer whale".
- **Akhlut** (Inuit orca-wolf) — *the most on-theme metaphor of the whole exercise*:
  a creature that **transforms across the sea/land boundary**, exactly like the
  app transforms **audio → text** and **capture → knowledge**. BUT it's
  pop-culture-saturated (MetaZoo card, tabletop minis, Ark mods, a Sonic comic
  general, plush toys) and folklore casts it as a fierce "kills men" predator.
  Evocative, but muddy + slightly menacing. Keep it as *lore/story*, not the name.
- **Aarluk** (Inuktitut) — brandable and clean in-category, but the Inuit-owned-
  business heritage above means tread carefully; verify trademark.
- **Kākahi** (Māori) — the "chiefs likened to it for intelligence" story is a
  lovely fit for an *intelligence* tool, but the mussel cross-meaning + macron
  (ā) add friction.

**How the best of these work for the app:**
- **Maki** → same playbook as Minke (a clean, ownable whale name) but *more*
  distinctive and memorable, and authentically "orca" rather than a different
  whale. Identity: simple orca mark, friendly palette. Tagline: *"Your meetings,
  remembered."* Best if you want approachable + ownable + genuinely orca.
- **Akhlut** (as concept) → if you ever want a transformation motif — fin that
  morphs into a soundwave / a mark that's half-fin, half-something — that's the
  Akhlut idea, usable even under a different name.

### Tier 4 — Great meaning, but already taken (avoid)

These are the "obvious" good names — listing them so we don't waste time:

- **Recall** — Microsoft Recall + Recall.ai (meeting recording API). ❌
- **Confluence** — Atlassian. ❌
- **Engram** — multiple notes/AI-memory apps, several with **MCP** (direct overlap). ❌
- **Mnemo / Mneme** — multiple "second brain" + voice-note apps. ❌
- **Steno** — several macOS voice-to-text apps. ❌
- **Murmur** — private-by-default macOS transcription app (our exact pitch). ❌ and avoid anything "Murmur-".
- **Cortex / Synapse / Glean** — generic AI / enterprise search incumbents. ❌
- **Capstan** — Celemony audio-restoration software. ❌
- **Cairn / Lodestar / Hearth / Quorum** — taken by adjacent productivity/B2B tools. ❌

## Finalist brand build-outs (2026-06-23)

Each finalist below is mapped to the product's real capabilities: multi-source
ingest (HiDock docks, USB recorders/SD cards, Plaud cloud), **fully local/offline**
transcription (Whisper / Parakeet / Sortformer), **speaker diarization**,
summaries, a knowledge graph, Obsidian sync, and the MCP server. Each leads with
a different identity pillar — pick the pillar you most want the brand to say.

---

### 🐋 Shachi — *the guardian* (leads with: local / private)

**The idea.** *Shachi* (鯱) is Japanese for orca and the root of the
**shachihoko** — the golden orca-guardian mounted on castle roofs to protect the
building. So the brand story is: *a guardian that sits on your machine and watches
over everything said in your meetings — and never lets it leave.*

**How it works for the app.**
- **Guardian = local-first/private.** The whole pitch is "all local, no network,
  no API keys." Shachi *protects* your conversations on-device — the opposite of
  the cloud bots that join your calls. The guardian metaphor sells the
  differentiator.
- **Orca = echolocation = transcription.** Sound in, meaning out — the core loop.
- **Dual nature** (real orca + mythic guardian) mirrors capture **+** intelligence.

**Identity.** A single stylised **golden orca-fin / shachihoko** mark, gold-on-ink.
Premium and unmistakable in a sea of pastel "AI notes" logos.
**Vocabulary.** Recordings live in your *pod*; Shachi "stands watch".
**Taglines.** *"Stands guard over every conversation."* · *"Your meetings,
watched over — on your machine."*
**Watch-outs.** Teach the say-it ("SHAH-chee"); don't let it read as "Sochi".
Respectful borrowing (everyday word + already a mainstream civic mascot). Run the
Class-9 trademark pass (none found in-category).

---

### 🟡 Saddlepatch — *the identity mark* (leads with: speaker diarization)

**The idea.** The **saddle patch** is the unique grey marking behind an orca's fin
that researchers use to tell every individual apart. That's *exactly* what the
app's diarization does: it gives **every voice its own identity**.

**How it works for the app.**
- **Speaker ID is a literal 1:1 map** — Sortformer diarization = "who said what".
  No other name on the list maps onto a real feature this precisely.
- **The pod** = your collection of devices/recordings.
- Quietly reinforces the local/private angle (you ID *your* people, on your machine).

**Identity.** The crescent **saddle-patch shape** — an abstract grey/white crescent
on black. Ownable, not a cartoon whale.
**Vocabulary.** Each recognised speaker is a *patch*; teaching a new voice =
"tagging a patch."
**Taglines.** *"Know every voice."* · *"Every speaker, identified."*
**Watch-outs.** Longest of the finalists (one word, 11 letters) — but very
ownable; `saddlepatch.app` almost certainly free.

---

### 🔺 Dorsal — *the silhouette that surfaces* (leads with: meeting intelligence)

**The idea.** The **dorsal fin** is the one part of the orca you see above the
water — and it's the most recognisable silhouette in the ocean. The brand idea:
most of the whale (your hours of audio, your archive) is **below the surface**;
Dorsal **surfaces** the part that matters — decisions, action items, the answer.

**How it works for the app.**
- **"Surface" is the verb of the product** — it turns hours of recording into the
  few things you act on (summaries, action items, search answers, MCP queries).
- **Depth = your private local archive**; the fin = the signal you actually see.
- Makes the **cleanest, most premium logo** of the set (a single tall fin).

**Identity.** One minimal **orca dorsal fin**, with an optional waveform as the
waterline.
**Vocabulary.** *Surface* (verb) — "surfaced action items"; *the deep* = your archive.
**Taglines.** *"Surfaces what matters."* · *"Hours below; the fin shows you what
counts."*
**Watch-outs.** Common anatomical word — do a trademark pass; on its own it's a
touch clinical, so the "surface/deep" story is what gives it warmth.

---

### 🌊 Minke — *the quiet companion* (leads with: personal / unobtrusive)

**The idea.** Minke whales are smaller, approachable and often curious and
solitary — not a pack-hunting "killer whale". That fits a **personal, quiet tool
that works in the background** (the mic-trigger silently arms; transcription runs
locally) rather than a bot that barges into your call.

**How it works for the app.**
- **Approachable + personal** = *your* tool on *your* machine, for one person's
  meetings — not enterprise call-bot energy.
- **Quiet** = the mic-trigger and local pipeline that just work without fuss.
- **Blank slate** — the cleanest availability of all, so total brand freedom.

**Identity.** A soft, simple whale/wave silhouette; calm palette.
**Vocabulary.** *Pods* still work for grouped recordings.
**Taglines.** *"Your quiet meeting companion."* · *"Listens, remembers, stays out
of the way."*
**Watch-outs.** Least *semantically* tied to "meetings/transcription" (it's a
lovely whale, but the meaning is carried by branding, not the word). Leans hardest
on brand-building — but it's the safest name to own.

---

### 🩶 Baleen — *the filter that takes everything in* (leads with: the multi-source hub)

**The idea.** A whale's **baleen** filters enormous volumes of water and keeps only
the nourishment. That's the app's job: **take in everything from every source** —
HiDock, USB recorders, SD cards, Plaud cloud — and **filter it down** to
transcripts, summaries and knowledge.

**How it works for the app.**
- **Best map to the multi-source hub + distillation** — ingest from anywhere, keep
  what matters. This is the pillar that most distinguishes the app from
  single-source notetakers.
- The whale keeps the listening/ocean identity intact.

**Identity.** A comb-like baleen motif or abstract whale-mouth intake.
**Vocabulary.** Your inbox = *the intake*; the app *filter-feeds* your recordings.
**Taglines.** *"Takes it all in. Keeps what matters."* · *"From every device, into
one record."*
**Watch-outs.** B2B namesakes (fintech/filters) → `.com` taken, use `baleen.app`;
slightly lesser-known word, needs a beat to land.

---

### Which pillar does each lead with?

| Finalist | Leads with | Best if you want the brand to say… |
|---|---|---|
| **Baleen** / Tributary | Multi-source hub | "everything flows in here" |
| **Saddlepatch** | Speaker diarization | "we know who said what" |
| **Dorsal** | Meeting intelligence | "we surface what matters" |
| **Shachi** | Local / private | "your conversations, guarded on your machine" |
| **Minke** | Personal / quiet | "your unobtrusive companion" |

## Availability & clearance pass (2026-06-23)

> Method: web search for App Store / GitHub / product collisions + a live-site
> probe of `<name>.app`. **Not** an authoritative WHOIS or trademark search —
> domain/TM verdicts below say "verify", and they mean it. Do a registrar lookup
> (Cloudflare/Namecheap) + USPTO/EUIPO (Class 9 software, Class 42 SaaS) before
> committing.

| Name | In-category clash? | Other namesakes (out of category) | `.app` probe | Verdict |
|---|---|---|---|---|
| **Saddlepatch** | None | None at all — only unrelated "Saddle*" repos (Scala lib, a DeFi protocol, a Minecraft server) | no live site (refused) → plausibly free | **Cleanest. Most ownable/defensible.** |
| **Shachi** | None | Only personal GitHub *usernames* (devs in Tokyo); no product | not probed | **Clear in-category.** `github.com/shachi` handle is taken → use an org like `useshachi`. |
| **Dorsal** | None | "Dorso" (a different word — macOS posture app); "dorsal" is a common anatomical word used incidentally everywhere | cert error → likely registered/parked | **Clear in-category, but weak to *own*** (generic word → hard to trademark/defend; `.com` surely taken). |
| **Maki** | None | Several dev-world namesakes: **Mapbox Maki** (well-known POI icon set), "Maki Software" (App Store dev), multiple GitHub repos | no live site (refused) | **Usable, not distinctive.** Muddy search/SEO; no in-category collision though. |
| **Minke** | None | **Minke Labs / minke-wallet** (a real crypto-wallet product), a microservices tool | not probed | **Usable, verify TM** — a live company already uses the exact word (different category). |

### Clearance ranking (easiest to own → hardest)
1. **Saddlepatch** — essentially unused anywhere. Best shot at a clean trademark + `saddlepatch.app`.
2. **Shachi** — clear in our category; only personal handles to work around.
3. **Dorsal** — clear in-category but a generic word, so weak to defend and `.com` is gone.
4. **Maki / Minke** — no in-category clash, but each has a real (out-of-category) product/company on the exact word, so distinctiveness + TM are murkier.

### Icon direction (one line each)
- **Saddlepatch** — the grey/white **saddle-patch crescent** on ink; abstract, ownable, not a cartoon whale.
- **Shachi** — a **golden shachihoko / orca-fin** mark, gold-on-ink; premium, guardian feel.
- **Dorsal** — a single **dorsal fin** with a waveform as the waterline; cleanest, most minimal.
- **Maki** — a **simple orca mark**, warm/friendly palette.
- **Minke** — a **soft whale-and-wave** silhouette; calm.

### Recommendation
For a name you can actually **own and defend**, the data points to **Saddlepatch**
(cleanest everywhere, and the tightest feature-story — speaker ID) or **Shachi**
(clear, richest identity via the shachihoko guardian). **Dorsal** is safe to *use*
but hard to *own*. **Maki/Minke** are charming but each already has a live company
on the exact word — fine for a personal/indie release, riskier if this ever
becomes a defended brand.

**Next:** a registrar + USPTO/EUIPO check on the top 1–2 (Saddlepatch, Shachi)
to confirm the domain and trademark are genuinely clear.

## Deep rationale — Saddlepatch

**Thesis:** the name should foreground the one thing that turns a pile of audio
into *usable* meeting knowledge — knowing **who said what**. A saddle patch is
exactly that: the mark that makes an individual identifiable. Most names describe
the *category* (notes, transcripts, recall); Saddlepatch describes the
*differentiator*.

**1. The biological truth.** A saddle patch is the unique grey-white marking
behind an orca's dorsal fin. No two are identical, so researchers identify every
individual whale — for life, across decades — from its saddle patch (plus the
fin's nicks). It's the basis of orca *photo-ID*: the catalogues that track whole
pods (e.g. the Southern Residents' J/K/L pods) rest entirely on the fact that the
patch is a fingerprint. Crucially, ID by saddle patch is **passive and
non-invasive** — you identify the animal just by observing it, no tag, no harm.

**2. Why it maps to *this* product (almost exactly).**
- **Saddle patch = speaker identity.** The app's hardest, most valuable step is
  diarization (Sortformer): attributing speech to people. Saddlepatch *names that
  step*.
- **Telling a pod apart = telling speakers apart** within a meeting and across
  your collection.
- **The photo-ID catalogue = a persistent speaker library** that recognises the
  same person across meetings over time — i.e. your knowledge base, but for people.
- **Non-invasive ID = local, passive capture.** You identify speakers on-device,
  no cloud, no bot sitting in the call — the same way you ID a whale by watching,
  not tagging it. The metaphor even carries the privacy story.

**3. It names the differentiator, not the category.** Every competitor
"transcribes meetings". A transcript without speakers is a wall of text;
*attributed* speech — who decided, who owns the action, who raised the risk — is a
record you can act on. Naming the product after the identity mark is a positioning
statement: we're not another transcriber, we're the layer that makes speech
*accountable*.

**4. It unlocks a whole, coherent vocabulary** (not a one-off pun):
- **Pod** — your collection of devices/recordings (you literally already have a
  pod of sources: HiDock + USB + Plaud).
- **Patch** — a recognised speaker; "tag a patch" = teach it a new voice; "your
  patches" = your people.
- **Resident** — local-first (resident orcas stay in home waters).
- **The deep** — your full private archive; the fin is the bit that surfaces.
This system names features naturally as the product grows.

**5. Brand strength & defensibility.**
- It's a **coined compound** — the strongest, most ownable trademark class (vs
  generic words like *Dorsal* or *Recall* that are nearly impossible to own). The
  clearance pass found it **unused anywhere** — clean App Store, GitHub, and
  category.
- It's **concrete and visual** → an instantly ownable, abstract mark (the
  saddle-patch crescent), not another cartoon whale or gradient blob.
- It sounds **nothing like** the Otter / Fireflies / "-ly / -AI" cohort, so it
  stands out in a crowded field — which is the entire job of a name there.

**6. Tone & mission.** "Patch" is warm, tactile, a little handmade (a badge, a
sticker, a quilt patch) — human, not cold enterprise SaaS. The promise writes
itself: **"every voice gets its saddle patch"** — inclusion (no contribution
lost), attribution (credit/accountability), recognition over time (it remembers
people). That's a mission, not just a tagline. Bonus easter egg for the technical
crowd: *patch* as in a software patch / *patching together* a record from many
sources — present but not the main meaning.

**7. It dodges the orca baggage.** It uses the *gentle, scientific, identifying*
part of the orca — not the predator. No "killer", no captivity/Blackfish
association, no Skana-style captive-whale ghost. Just the mark that says *this one
is her, and we know her*.

**8. Honest counterpoints (and why it holds).**
- *"11 letters / two words."* — Compresses to "Saddle" in speech and to the
  patch/pod vocabulary; one word, trivially spelled once heard. Long distinctive
  names thrive (Mailchimp, Squarespace, Basecamp).
- *"Is the orca link too obscure?"* — It doesn't need to be understood to work
  (it's friendly and brandable on its own), but it *rewards* a second look with a
  rich story — the hallmark of a durable brand. Great onboarding/marketing hook.
- *"Pronunciation?"* — Two common English words; zero coaching needed (unlike
  Shachi).

**9. Strategic fit.** The app is becoming attributed, queryable meeting memory
with an MCP layer for AI agents. An agent answering *"what did Sarah commit to?"*
needs **speaker identity** as its substrate. Naming the product after identity
signals the roadmap: not transcription, but a memory of *who* said *what* — which
is exactly what makes the knowledge graph and the agents useful.

**Identity snapshot.** Mark: the grey/white **saddle-patch crescent** on deep ink
(abstract, ownable). Voice: precise but warm. Line: *"Know every voice."* /
*"Every voice gets its patch."* Vocabulary: pods, patches, residents, the deep.

**One-paragraph pitch.** *Saddlepatch turns the recordings from every device you
own — HiDock, USB recorders, Plaud — into a private, local record of your
meetings where every voice is identified, the way researchers know each orca by
the unique patch behind its fin. Not another transcriber: the layer that makes
what was said accountable, searchable, and yours.*

## A favourite, if forced to pick

- **Tributary** — best *describes* what it is (everything flows in) and is clear.
- **Minke** — best if you want the whale identity, and it's a blank slate.
- **Baleen** — best of both: the multi-source/listening metaphor *and* the whale
  theme, clear in-category.

Naming pattern either way: lead with the single word, pair with a plain
descriptor for store listings / SEO, e.g. *"Tributary — local meeting memory"* or
*"Minke — your meetings, transcribed on-device"*.

## Completed
- [x] Defined the naming brief from the current product (3 pillars).
- [x] Brainstormed ~30 candidates across water-flow, memory/brain, conversation,
      tape/recording, and cetacean themes.
- [x] Web clash-checked the shortlist against the transcription/meeting/notes/
      AI-memory category.

## Planned / next steps
- [ ] User picks a direction (water-flow vs whale vs other).
- [ ] **Formal clearance** on the chosen name: USPTO + EUIPO (Class 9 + 42),
      app-store search, and domain availability (`.app` likely; `.com` may need a
      modifier).
- [ ] Decide scope of the rename: product/display name only, or also repo,
      bundle IDs (`com.hidock.tools.*`), Keychain service strings, GitHub repo,
      release artefact names, README/PARITY. (A bundle-ID change has migration
      cost — keychain/settings keyed on the old ID — so likely keep IDs and only
      rebrand the display name + repo initially.)

## Rejected / not applicable
- Anything starting **HiDock-** or **Plaud-** — the app is multi-device; tying it
  to one vendor is the problem we're fixing.
- "AI"-prefixed or "-ly/-fy" coinages — would sit indistinguishably among the
  incumbents listed above.

## Clash-check sources
- Transcription/meeting landscape: [Otter](https://otter.ai/), [Fireflies](https://fireflies.ai/), [Notta](https://www.notta.ai/en), [Voicenotes](https://voicenotes.com/), [VOMO](https://vomo.ai/), [Limitless](https://otter.ai/) (Pendant, via Notta roundup), [Zapier roundup](https://zapier.com/blog/best-transcription-apps/)
- **Murmur** (closest positioning match): [murmur.you](https://murmur.you/), [trymurmur.app](https://www.trymurmur.app/en)
- **Steno** (macOS voice-to-text): [stenovoice.app](https://stenovoice.app/docs), [stenofast.com](https://stenofast.com/), [meetsteno.com](https://www.meetsteno.com/)
- **Engram**: [engramapp.com](https://www.engramapp.com/), [engram.page](https://engram.page/), [engram-mcp.com](https://engram-mcp.com/)
- **Mnemo/Mneme**: [Mnemo — My Second Brain (App Store)](https://apps.apple.com/us/app/mnemo-my-second-brain/id6756796222), [mneme (GitHub)](https://github.com/ppserapiao/mneme)
- **Orca**: [GNOME Orca screen reader](https://orca.gnome.org/), [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer)
- **Capstan**: [Celemony Capstan](https://www.celemony.com/en/capstan)
- **Baleen** (namesakes, none in-category): [Baleen Solutions acquisition](https://www.prnewswire.com/news-releases/xactus-acquires-baleen-solutions-expanding-intelligent-verification-capabilities-for-self-employed-income-analysis-302676461.html), [Baleen entity-extraction tool (Dstl, GitHub)](https://github.com/dstl/baleen)
- **Quorum**: [quorum.us](https://www.quorum.us/products/mobile-app/)
- **Cairn**: [cairntrail.com](https://www.cairntrail.com/apps/stack), [cairnsoftware.com](https://support.cairnsoftware.com/kb/root.aspx)
- **Hearth**: [hearth.sh](https://hearth.sh/)
- **Lodestar**: [lodestarbpm.com](https://secure.lodestarbpm.com/)
- **Narwhal**: [narwhal.app (Reddit client)](https://narwhal.app/)
