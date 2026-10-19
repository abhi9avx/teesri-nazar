# PRD — Faster screen reading and a self-learning navigation map

| | |
|---|---|
| Status | **Draft v0.2** — revised after three reviews (grilling, product, design + TDD); for founder review. Nothing built. |
| Branch | `feature/nav-map` (never built on `main`) |
| Date | 2026-10-19 |
| Test plan | [test-plan-nav-map.md](test-plan-nav-map.md) |
| Related | ARCHITECTURE.md §6 (reading the screen), §7 (selectors), §11 (exit codes), §14 (replay), §15 (dashboard, app map) |

Examples use generic banking and shopping screens (Passbook, Active loans, Orders). No customer app
content belongs in this document, the tests or the fixtures.

**What changed from v0.1:** the read-timing table and `last-screen.xml` facts were wrong against the
code and are corrected (§6 A3, §7 B2); hops are verified by screen identity, not by a title wait (a
false-arrival hole); recording is learned from the run's own event log instead of a cross-process state
file; trust is simplified (no formula, no tiers); `back`/`launch` recovery and interstitial
auto-dismiss are cut; evidence, kill criteria and a cheap prompt-only experiment are added; founder
decisions are separated from engineering ones (§12).

---

## 1. Problem

Today the agent sees **only the screen that is open**. `observe` prints the filtered accessibility tree
of that one screen. To act on "open my passbook" or "check the statement of my active loan", the agent
guesses from visible labels, taps, observes again and repeats: **about one model turn per screen, every
time, for every tester**. The only memory is hand-written `flow`s in `plans/*.plan`, saved only when the
agent remembers to.

Consequences:

- **Slow and expensive** on repeat visits to screens whose route never changed.
- **Wrong turns** when labels differ from the user's words ("Passbook" may be "Statement",
  "Transactions", or an unlabelled icon).
- **Nothing is learned**: a mistake made today is made again tomorrow, by every tester.
- **Thin trees** on some UIs: unlabelled icons print as a bare class name; Compose without ids,
  WebView and Flutter expose little.

**Evidence: [ASSUMPTION] — not measured yet.** All local runs so far are the founder's own, and
there are no customer interviews. Phase 0 (§10) measures it before Part B is built past its first
slice:

> *Navigation share* = model turns and taps between `case start` and the case's first passing
> `check`, divided by all turns in the case, across ≥ 20 exploratory cases on ≥ 2 apps
> (benchmark with the map off, §9).

## 2. Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | A route found once is not explored again while the app has not changed | map hit rate (§9) |
| G2 | A known route runs in **one command, no model turn per hop** | turns and wall time per navigation |
| G3 | The map is **learned automatically** while the agent works; nobody writes it by hand | share of routes learned with no manual step |
| G4 | It **learns from mistakes and new builds**: a broken route works again after one exploration | self-heal rate |
| G5 | **Never worse than today.** No map, a miss, or the map off cost the same reads, turns and output as today | pinned tests (§test plan 3) |
| G6 | **Never a false pass.** A learned route can stop or fail, never make a case pass or land on the wrong screen unnoticed | every hop verified by screen identity |
| G7 | Works on **any Android app** with no per-app code | benchmark across differently built apps |

## 3. Non-goals (v1)

- An autonomous crawler (money-moving screens, real accounts).
- Any AI, embeddings or network calls inside `tnazar`. The agent (an LLM) does meaning-level matching.
- Team sharing — v1 is **local to one machine** (v1.1, §10).
- Automatic recovery from an unknown start screen (`back` presses, relaunching) — unsafe on forms and
  payment screens; the agent decides.
- Auto-dismissing permission or system dialogs — it changes the app under test.
- Replaying routes that need typed input (search boxes, forms).
- `goto` inside plan files — plans stay fixed and reviewable; replayed regression plans do not benefit.

## 4. Users and scenarios

**Persona:** a QA engineer testing **their own app** (staging build, test accounts) through the agent,
sometimes watching the dashboard.

| # | Scenario | With this PRD |
|---|---|---|
| S1 | First time anyone asks "open passbook" | `goto passbook` misses and prints the current screen (no extra read, no extra turn); the agent explores once; the route and the name are learned |
| S2 | Asked again, same run or a later run | `goto passbook` arrives in one command, each hop verified |
| S3 | Asked differently ("statement", "transactions") | agent reads `tnazar map`, sees `Passbook (aliases: statement)`, calls `goto passbook`; or explores once and adds the alias |
| S4 | Passbook reachable from Home → Savings → Passbook **and** Profile → Statements → Passbook | route planned **from the screen the user is on now**, fewest hops |
| S5 | "go to passbook" asked 10 times | one screen, one edge, one name; repeats only add counts |
| S6 | New build renames "Savings" to "Accounts" | `goto` stops at the broken hop and names it; agent explores from there; the new hop is learned and preferred; the broken one retires after repeated failures |
| S7 | "check the statement of my active loan" | agent: `goto active loans` (known) → explores the last step once → names it; next time `goto loan statement` runs the whole path |
| S8 | No map yet / map off | exactly today's behaviour |
| S9 | Route passes through Pay / Transfer / an MPIN screen | recorded as knowledge, never replayed |
| S10 | New team member, empty map | map seeded from existing `plans/*.plan` flows on first use, so day one is not zero |
| S11 | Tester wants to see before trusting | `goto passbook --dry-run` prints the hops and why this route was chosen; `tnazar map` lists what was learned |
| S12 | Tester says "that's not passbook" (chat or dashboard) | agent runs `map forget passbook` or `map name <right screen> passbook` |
| S13 | Second tester on another laptop | **not shared in v1** — said plainly at `start` and in the docs |
| S14 | Start screen not in the map (half-way through a form) | `goto` exits 2 and prints the current screen; the agent decides (no blind `back`) |

## 5. Principles

1. **Never worse.** Default outputs, exit codes, plan syntax, old-run replay and screen reads per
   command stay identical. New behaviour = new commands or opt-in flags. Recording overhead ≤ 20 ms per
   command, zero extra screen reads.
2. **Verify by identity.** Every replayed hop waits until the screen *is* the expected screen (§A1), not
   until some label shows.
3. **Lose an edge rather than learn a wrong one.** Any doubt while recording → record nothing.
4. **Local-first, no customer data**, and disclosed (`start` prints where the map is stored).
5. **The agent decides meaning; tnazar decides facts.** Names match exactly (after normalising) or by
   alias — no substring guessing.
6. **Hand-written flows win** over learned routes.

---

## 6. Part A — Better screen reading

### A1. Screen identity: `Screen.identity()`

`Screen.signature()` (screen.py) hashes every id, text and description, so a changed balance makes the
same screen look new; scroll-end detection and report grouping depend on that, so it stays. A new
`identity()` answers "which screen is this?". (Named `identity`, not `fingerprint`: tests already use
"fingerprint" for the report's `screen_sig`.)

- **Tokens** from the elements `observe` shows, **outside scrollable containers**: `class:short-id`
  when there is an id, else `class:role:label` (role = tap / edit / check).
- **Scrollable containers** contribute only their own id and class — never their rows. (Rows are
  dynamic and hold customer data: merchants, transactions.)
- **Normalise:** digits → `#`; masked secrets removed; EditText values and password fields dropped;
  labels over 24 characters dropped.
- **Title:** the toolbar title or selected tab label, scrubbed (§B9). Shown to humans.
- **Same screen** when token-set Jaccard ≥ 0.8 **and the titles are equal**. Different titles never
  merge (otherwise Passbook ≡ Loan statement and "already there" would lie).
- **Canonical:** the first-seen token set is stored and never updated; new reads are compared only
  with stored canonicals (similarity is not transitive). Screen id = 8 hex of sha1 of the canonical
  tokens.
- **Package** = majority package of interactive nodes (not the first node, which may be the keyboard).
- **Kinds:**
  - `opaque` — dominated by one WebView / Flutter / canvas view. Never stored; **transparent** for
    recording (does not end or clear a pending action).
  - `loading` — a progress indicator and nothing else interactive. Transparent, never stored.
  - `foreign` — no element of the app's own package (a system permission dialog, the launcher,
    another app). Transparent, never stored, never dismissed. Keyboard nodes (another package) are
    ignored when identifying the app screen under them; an in-app dialog or bottom sheet is an ordinary
    screen of its own.
- Tokens are stored **hashed**; plain text is kept only for tap selectors and the title.

### A2. Navigation habits (tester prompt, no code)

In CLAUDE.md:

1. Before exploring, run `tnazar goto "<what the user asked>"`. A miss already prints the screen —
   carry on from it.
2. On an unknown screen try the bottom navigation and the menu / profile first, then in-app search
   (routes through typed search are not replayable, so they never become fast).
3. After reaching a requested screen by exploring, `check` it; if the name did not bind, `map name`.

### A3. `last-screen.xml` (behaviour pinned, unchanged)

Corrected from v0.1: only `observe`, the reading forms of `scroll`, and `tap`/`type --into` with
`--wait 0` write `last-screen.xml`. Plain `tap X` and `type --into X` default to `--wait 3`, which reads
through the device poll and does **not** write it; neither do `wait` and `check`. So `#n` refers to the
last `observe`. This is pinned by a test and not changed. The map does not depend on this file.

### A4. Observe extras (v1.1, opt-in, independent of the map)

- `observe --diff`: only what changed since the last `observe` (`+`/`-`/`~`), keeping original `#n`.
- `observe --hints`: for unlabelled clickable elements, the nearest label and a `x%,y%` usable by
  `tap`; on opaque screens one line suggesting `observe --shot`.

They do not depend on the map and ship after the map slice (cheap, separate value).

---

## 7. Part B — The self-learning navigation map

### B1. Data model

| Object | What it is | Dedup key |
|---|---|---|
| **Screen** | one app screen by identity (A1) | canonical id |
| **Edge** | "on screen X, this one action leads to screen Y" | (from, to, action selector) |
| **Name** | a phrase meaning a screen ("passbook") | (normalised phrase, screen) |
| **Count** | per edge/name: successes, failures, distinct runs, last seen, last result, app version | — |

### B2. When to record (corrected against the code)

**How commands read the screen today:**

| Reads **before** acting | Reads **after** acting (polls until satisfied) | Reads **nothing** |
|---|---|---|
| `tap <selector>`, `type --into <selector>` (also `--wait N`), `scroll --within`, sideways scroll | `wait`, `check`, `scroll --to`, plan `if` | `tap '#n'`, `tap x,y` / `x%,y%`, `type` without `--into`, `key`, `swipe`, `launch`, `open-url`, `check --result`, `adb` |

Plus `observe`, which only reads.

**Mechanism — learned from the run's own log, not a live state file:**

1. Each *settled* read — the read a command acted on, or the last poll of a `wait`/`check` that
   succeeded — is stamped with its screen id on that command's event. `observe` gets a hidden event
   for this (the dashboard and replay skip it). Scrubbed token sets go to `runs/<run>/screens.jsonl`.
2. A pure function `learn(events, screens)` turns the run into map updates. It runs at `stop`, at the
   next `start` for an unstopped run, and on demand inside `goto` for the current run (so a route found
   ten minutes ago is usable now).
3. This replaces v0.1's cross-process `.tnazar/nav.json`: no races, nothing leaks between runs, dashboard
   typing is visible (it is already logged as an event), and every rule below is a unit test.

**Rules (read in order):**

| Situation | Learned |
|---|---|
| Settled read on screen X, then **one navigating action**, then the next settled read on screen Y ≠ X | **edge X → Y** with that action's selector |
| Navigating action = `tap <selector>`, or `scroll --to` followed by its tap | — |
| Next settled read is the **same** screen X | nothing; the pending action is dropped (no-op or toggle tap) |
| Two or more actions before the next settled read | nothing (ambiguous) |
| Read in between is `opaque` or `loading` | transparent: keep waiting for the next real screen |
| Pixel / `#n` / percent tap | the edge is stored **as knowledge only**, never replayed (no stable selector) — `#n` taps are resolved to the element's selector when one exists |
| `type`, dashboard "type it now", or any secret entry in between | nothing (input-dependent; an OTP may auto-submit) |
| `key back`, "Navigate up", close-X | knowledge only, never routed (depends on history) |
| `launch --restart` / `--fresh`, then a settled read on Y | edge `@cold` → Y (start node) |
| Plain `launch`, `open-url` | breaks the chain (resume lands anywhere) |
| More than 60 s between the action and the next settled read | nothing (timers, redirects, human touches) |
| `overlay` screen between X and Y | edges X → overlay and overlay → Y, as observed; `goto` never dismisses it, only taps what was recorded |
| `wait A --or B` exits 4 | failure count on the edge expected to reach A |
| `tnazar run <plan>`, `flow` | recorded like hand-driven steps (same `cli.main` path) |
| `replay` | **not recorded in v1** (it bypasses `cli.main`; a dedicated hook later) |
| `TNAZAR_MAP=off` | nothing; outputs identical |

### B3. No duplicates

- Screens matched to stored canonicals before insert; a revisit adds nothing.
- Edges and names keyed (B1); a repeat updates **counts**, never adds a row.
- Distinct-run counting: asking "go to passbook" 10 times in a run counts as one run.
- **Phrase normalising:** lowercase, trim, drop leading `open`, `go to`, `show`, `check`, `my`, `the`,
  collapse spaces. "Open my Passbook" = "passbook". Flow names are normalised the same way
  (`open_settings` → "settings").
- Duplicates in the append-only file are folded when the map loads.

### B4. How screens get names

1. **Search episode.** `goto <unknown phrase>` exits 2 and opens an episode for that phrase. The episode
   closes at the next `goto`, `case start`/`case end`, `launch --restart`, `stop`, or after 25 actions.
   On close, the phrase binds to the screen of the **last** passing `check <selector>` made in the
   episode on a screen other than where it started. `check --gone`, `check --result` and checks on the
   start screen never bind.
2. **Explicit:** `tnazar map name <screen-id|here> "passbook" [--alias statement …]` — the correction
   and override path.
3. **Conflict:** one phrase bound to two screens → `goto` exits 2 listing both; the agent asks the user
   or chooses by context.
4. **Never** learned from `say` text or case titles.
5. **Seed:** on first use for an app, each `flow` in `plans/*.plan` for that package becomes a name
   (normalised flow name) bound to the flow, which `goto` runs as is.

### B5. Routing from where the user is (multiple entry points)

1. `goto` reads the current screen (one read) and matches it to a stored screen.
2. **Already there** → exit 0, no taps.
3. **Start screen unknown** → exit 2, print the current screen as `observe` would (no extra read) and the
   names known for this app. No `back` presses, no relaunch.
4. Otherwise: shortest path (BFS, fewest hops) from the current screen to any screen bound to the
   phrase, using only **runnable** edges (B6). A hand-written flow with the same name wins.

### B6. When a learned edge may run (simplified)

An edge is **runnable** when all hold:

- it has a stable selector, and is not `type`, back/up, or pixel-only (B2);
- it is **safe** (B9);
- it has **not failed in this run**;
- it is not **retired** — retired after 2 consecutive failures on the current app version; a later
  success un-retires it.

A route learned once — even earlier in the same run — runs automatically. That is safe because every
hop is verified by identity (B7): a wrong edge costs one hop timeout and an explanation, never a false
pass. *(Founder decision F2 — the alternative is "only after 2 runs".)*

`goto --dry-run` prints the route, each hop's counts and last result, and why it was chosen, without
touching the device beyond the one start read.

### B7. Fast execution

`goto` runs its own hop loop (it does **not** compile to `wait <title>` steps — the tapped label often
equals the next screen's title, so a title wait would pass on the starting screen):

1. Read the screen (once at start; afterwards the last read of the previous hop).
2. Tap the hop's selector on the screen just read.
3. Poll until the screen identity **changes from the source and equals the hop's target** → next hop.
   Fail fast if another known screen is stable for 2 consecutive reads; otherwise fail at the hop's
   timeout (default 10 s).
4. On arrival at the last hop, exit 0.

- **One CLI call, zero model turns per hop.** Uses the fast UiAutomator2 reader (~0.05 s per read) when
  present; polls, never sleeps.
- **Reads:** 1 start read + the polls of each hop (≥ 1 per hop) — never more than the same route written
  as a hand-written flow.
- **Target:** ≤ 1.5 s per hop on the fast reader.
- Registered like `flow` (`device=True, action=False`): it takes the device lock per hop, so dashboard
  hold (exit 3) and pause (exit 5) work between hops; each hop is logged as a normal `tap` event (old
  dashboards and replay understand it); an inner exit 4 maps to 1.
- A **`nav` event** at start and end (phrase, route, hops, outcome, duration) feeds metrics (§9).

### B8. Learning from mistakes and new builds

1. A hop fails → `goto` stops with exit 1 and prints: the hop, the expected screen, the current screen
   as `observe` would (no extra read). Cost ≤ one hop timeout.
2. The failed edge gets a failure count and is not used again in this run.
3. The agent explores from there; B2 learns the new edge; BFS prefers it next time. Nothing is patched by
   hand.
4. Two consecutive failures on the current app version retire an edge; it stays in history.
5. Unattended re-exploration and automatic re-planning: not in v1.

### B9. Privacy and safety

- **Stored:** hashed identity tokens; plain text only for tap selectors and titles, each ≤ 24 chars,
  digits → `#`, secrets masked (`env.mask`). **Dropped:** EditText values, password fields, anything
  inside scrollable containers, labels matching email / phone / currency / long digit runs.
- **Disclosure:** `start` prints `map: learning routes into ~/.tnazar/maps/<package>` (or `map: off`).
- `tnazar map audit` prints every plain-text string stored, for a human check.
- **Unsafe edges** — selector or target screen matching pay, send, transfer, confirm, buy, withdraw,
  invest, delete, submit, or a target screen with a password/MPIN field — are stored as knowledge,
  **never run**. Recomputed on every load, so an edited file cannot mark one safe. Extra words per app
  in `testdata.yaml` (`map_unsafe_words`) for local language and bilingual labels.

### B10. Storage and maintenance

- **Where:** `${TNAZAR_MAP_DIR:-~/.tnazar/maps}/<app package>/map.jsonl` — one directory per package
  (`.dev` and `.test` builds are separate apps), outside any git repo.
- **Format:** append-only JSON Lines, header line with schema version and package; folded in memory on
  load (a few hundred screens, a few thousand edges). Chosen over SQLite: inspectable, crash-safe by
  append, concatenates for future sharing, matches `events.jsonl`.
- **Locking:** appends and compaction take a lock on a separate `map.lock`; a torn last line is
  repaired with a newline before appending; if locking is unavailable (network drive) recording is
  skipped with one warning.
- **Manual maintenance in v1:** `map compact` (fold history, atomic rewrite), `map forget`, `map audit`.
  Automatic pruning: later, when files are actually large.
- **Corrupt line or file / unknown schema:** skipped with one warning; never blocks a run or changes an
  exit code.

### B11. CLI

| Command | Does | Exit |
|---|---|---|
| `tnazar map [--edges]` | screens for the run's app: id, title, names, edge counts, unsafe marker; `--edges` lists edges | 0; 2 no run |
| `tnazar map name <id\|here> <phrase> [--alias …]` | bind a phrase | 0; 2 unknown screen |
| `tnazar map forget <name\|screen\|edge>` | tombstone | 0; 2 not found |
| `tnazar map compact` / `map audit` | maintenance / privacy listing | 0 |
| `tnazar goto <phrase\|screen-id> [--dry-run]` | route and run (B5–B8) | 0 arrived (verified); 1 hop failed; 2 unknown phrase, unknown start, no runnable route, or conflict; 3 held; 5 paused |
| `TNAZAR_MAP=off` | disables recording and `goto` (prints `map: off`) | — |

### B12. Dashboard

- `goto` narrates a heading: `goto passbook — learned route, 2 hops` (or `— flow`).
- Hops are normal `tap` rows.
- A failed hop is a red row: `expected Passbook, got Offers`.
- Acceptance line **DA-13** in ARCHITECTURE.md, mapped to tests.

---

## 8. What stays exactly the same (pinned by Phase 0 tests)

Default `observe` output; `#n` meaning; `last-screen.xml` writers (A3); screen reads and adb calls per
command (map on vs off); `signature()` and scroll-end detection; `events.jsonl` for existing event types;
exit codes 0–5 for existing commands; plan syntax; replay of old runs; old timelines render unchanged.

## 9. Success metrics and kill criteria

**Instrumentation:** the `nav` event (B7) for `goto`; a baseline proxy for exploring (turns and taps
from `case start` to the first passing `check`); `usage.per_case` for tokens. Baseline = the benchmark
with the map off (test plan §7), not old run history (it has no screen ids).

| Metric | Definition | v1 target [ASSUMPTION, revisit after Phase 0] |
|---|---|---|
| Map hit rate | navigations ending on a screen already in the map that `goto` served ÷ all such navigations | ≥ 50% from the 2nd benchmark run |
| Turns per known navigation | model turns from request to arrival | 1 |
| Wall time per hop | `goto`, fast reader | ≤ 1.5 s |
| Self-heal | benchmark routes broken by a renamed build that work after one exploration | ≥ 70% |
| **Wrong-screen rate** | `goto` exit 0 but the benchmark's ground-truth check fails | **0** — any > 0 blocks release |
| Never-worse | pinned tests with the map on and off | all green |

**Kill criteria [ASSUMPTION]:**

- Phase 0 navigation share < 20% of case turns → stop Part B after the first slice; ship A2 + A4 only.
- After 3 warm benchmark runs on 2 apps: hit rate < 30%, or > 1 in 10 `goto` calls fail a hop → rethink
  identity/recording before going further.

## 10. Rollout (all on `feature/nav-map`)

| Phase | Content | User-visible |
|---|---|---|
| **0 Safety net** | characterisation tests (corrected), fake-device extensions, public-app fixtures, benchmark harness, map-off baseline | baseline numbers |
| **0.5 Prompt-only experiment** (≈1 day) | prompt tells the agent to `flows` first and save a `flow` after every exploration to a named screen | does route reuse matter? |
| **1 Identity + learning** | A1, B2 learning, B3, B9 scrub, B10 store, disclosure line, `map` listing | "here's what I learned about your app" |
| **2 goto** | B5–B8, B4 names + seeding, B11, B12, A2 prompt habits | one-command navigation |
| **3 Gate** | device smoke, benchmark map off vs on, review | numbers vs targets |
| **v1.1** | team sharing by `map export`/`import` into the customer's own test repo (one file per machine); A4 observe extras; global edges (a bottom-nav tap valid from any screen showing it); replay recording | — |

Every phase: tests on Python 3.9, one AI review, live smoke before any push (repo done-checklist).

**Estimate (engineer-days, design review):** Phase 0: 3 · 1: 4–5 · 2: 6–8 · 3: 3–5 → **≈ 16–21
days** for v1 (v0.1 scope was ≈ 21–26).

## 11. Risks

| Risk | Mitigation |
|---|---|
| Identity unstable (Compose without ids, A/B copy) → routes never reused | no rows in identity; title + Jaccard; fixtures per UI type; kill criterion |
| Two screens merged → false "already there" | titles must match; per-hop identity verification; wrong-screen metric |
| Wrong edges from timing (animations, delayed navigation, auto-submit) | one action per edge, settled reads only, 60 s cap, inputs void the edge |
| Customer data in the map | no rows, hashed tokens, scrub, `map audit`, privacy test over every byte |
| Money-moving replay | unsafe never runs, recomputed on load, per-app word list |
| Testers don't trust it | `--dry-run`, `map` listing, one-line failure explanation, disclosure |
| Navigation is a small share of effort | Phase 0 measurement + Phase 0.5 experiment before Phase 2 |
| Single-machine map doesn't show the team moat | v1.1 sharing; F1 decides whether to pull it into v1 |

## 12. Decisions

### Founder decisions

| # | Question | Recommendation |
|---|---|---|
| F1 | Team sharing in v1 (the moat visible to a first design partner) or v1.1? | v1.1, unless a design partner is lined up |
| F2 | A route learned once runs automatically (per-hop verified), or only after 2 runs? | once |
| F3 | Recording on by default (with the disclosure line)? | yes, `TNAZAR_MAP=off` to disable |
| F4 | Does a map ever leave the customer's machine/repo (hosted, business model)? | no, not in v1 or v1.1 |
| F5 | Which design partner validates this, and when? | before Phase 2 |

### Engineering decisions (taken, reviewable)

| # | Decision |
|---|---|
| E1 | Build variants are separate maps |
| E2 | Unknown start screen → exit 2 with the screen printed; no back/relaunch |
| E3 | `last-screen.xml` behaviour unchanged (A3) |
| E4 | Recording learned from the run log at `stop`/on demand, not a live state file |
| E5 | No dismissing of permission/system dialogs |
| E6 | Max 60 s between an action and its arrival read |
| E7 | Unknown phrase, unknown start, no route and conflict all use exit 2 (no new exit code) |

---

## 13. Edge cases (analysed before building; each has a test)

### Names (what the user says)

| # | Case | Behaviour |
|---|---|---|
| N1 | "open passbook" asked 3×, 30×, in one run or many | one name row per (phrase, screen); counts once per run; every repeat is a `goto` hit, no new rows |
| N2 | "Open my Passbook" / "go to passbook" / "PASSBOOK" / "the passbook" | normalised to `passbook`: the same name |
| N3 | misspelt: "pasbook", "sorbook" | a miss never guesses. `goto` prints `did you mean: passbook?` (close known names, stdlib `difflib`) and the screen; it never runs a fuzzy match. The prompt tells the agent to correct the spelling against `tnazar map` before calling `goto` |
| N4 | misspelling still explored and checked on the right screen | bound as an extra name of the right screen: harmless |
| N5 | wrong binding: the agent checks the wrong screen for "passbook" | it binds there. Correct with `map name <right screen> passbook` (an explicit name overrides every learned binding of that phrase) or `map forget passbook`. A later learned binding of the same phrase to a different screen is a **conflict**: `goto` exits 2 listing both, never picks one |
| N6 | the phrase is also a flow name (`open_settings` ↔ "settings") | the hand-written flow runs |
| N7 | the phrase normalises to nothing ("open", "go to") | exit 2 "say which screen" |
| N8 | a phrase that is a screen id | the screen id wins (ids are 8 hex characters) |
| N9 | multi-step phrase ("statement of my active loan") | exact phrase only; bound to the final screen after one exploration |
| N10 | episode with no passing check, or only `--gone` / `--result` checks, or checks only on the start screen | nothing bound |

### Screens

| # | Case | Behaviour |
|---|---|---|
| E1 | same screen visited many times, scrolled, balance changed | one canonical screen (rows and digits excluded) |
| E2 | same layout, different titles (two settings pages) | different screens |
| E3 | two screens with no ids or titles that look alike | may merge (residual risk); every hop still waits for a *changed* identity, and the benchmark's wrong-screen metric guards it |
| E4 | keyboard open vs closed | the same screen (keyboard nodes are another package) |
| E5 | loading spinner, WebView/Flutter canvas, system dialog | transparent: never stored, never end an edge |
| E6 | locale or dark-mode change | labels differ → new screens; routes re-learn |
| E7 | different test accounts show different screens | different screens; a route that does not fit fails at the hop, never passes wrongly |

### Routes

| # | Case | Behaviour |
|---|---|---|
| R1 | two or more ways into a screen | fewest hops from the screen the user is on |
| R2 | start screen unknown / transparent | exit 2 with the screen listing; no blind `back` |
| R3 | already on the target | exit 0, no taps |
| R4 | the hop's button is below the fold | the edge remembers the `scroll --to` that found it; `goto` scrolls the same way (bounded) before tapping |
| R5 | toggle / no-op tap | same screen → no edge |
| R6 | money-moving or MPIN target | stored, never run |
| R7 | back / up / typed / pixel-only actions | knowledge only, never run |
| R8 | new build renames a button | hop fails, exit 1 naming it; not retried this run; retired after 2 consecutive failures on that version; the explored replacement is used next time |
| R9 | session expired: login screen appears mid-route | another known screen stable for 2 reads → fail fast, exit 1 |
| R10 | dashboard message or pause mid-route | stops between hops, exit 3 / 5 |
| R11 | run without `--package` | nothing learned; `goto` exits 2 explaining why |

### Storage

| # | Case | Behaviour |
|---|---|---|
| S1 | map off | identical output, reads and adb calls; nothing written |
| S2 | corrupt line, unknown schema, unwritable directory, no file locking | warning once; the command's result and exit code unchanged |
| S3 | two runs on two devices learning the same app at once | appends under a lock; nothing lost or torn |
| S4 | a run stopped twice / learned at `stop` and again at the next `start` | learned once (a per-run marker) |
| S5 | `.dev` and `.test` builds | separate maps |

---

## 14. As built (feature/nav-map) — where it differs from the text above, and why

| Area | Built | Why |
|---|---|---|
| A1 identity | A screen is its **frame** (title, buttons and labels outside lists, the selected tab, list containers) plus its **content** (ids inside lists). Titled screens match on title + selected tab + frame overlap ≥ 0.6; untitled screens only exactly | On the emulator, Settings pages are one scroll view sharing every row id (0.8 overlap without titles), and the Internet page's list changes while it scans: content cannot identify a page |
| A1 kinds | No `overlay` kind: an in-app dialog is an ordinary screen; another package's window is `foreign` (transparent) | Simpler; a system dialog is never a place to start or end a route |
| B2 selectors | A tap records the element's unique id (`stable`) next to the typed selector; learned steps use the id | A relabelled button (same id) keeps working |
| B2 labels | A step whose label or selector holds digits, currency, `@`, a `.env` value or > 24 characters is knowledge only (never replayed) | The label is what says a step moves money ("Place order ₹1,299"); without it the step cannot be judged safe |
| B2 back | Taps on "Navigate up", Close, Cancel, Back, Done are knowledge only, like `key back` | Where they lead depends on history |
| B2 `#n` | An `#index` tap is not stamped with a screen | It points into the last `observe`, which may be stale |
| B4 names | The search ends at the passing check that says every word of the phrase; else the check saying most of it; a check saying none of it counts only if the agent did not press Back after it. A `goto` answered by a saved flow also ends a search | A later unrelated check stole the name in testing |
| B9 unsafe | Also: a screen whose title says pay / transfer / confirm / checkout / withdraw / enter PIN is never a target. Extra words: `TNAZAR_UNSAFE_WORDS` (comma list) instead of `testdata.yaml` | Titles are hashed, so this is decided when the screen is read |
| B6 retire | Counts only results on the current app version | A new build gets a fresh chance |
| A1 untitled pages | An untitled page is recognised by its **menu**: hashed labels of short buttons inside its lists without digits. A read and a screen of which only one shows a title match on the menu alone, and only when one stored screen fits | Real profile/FAQ pages have no title and are all one scroll view; a top label comes and goes between visits |
| A1 privacy | Menu labels are stored as 10-hex SHA-1 hashes; a short, guessable label (a payee name in a list) could be recovered by a dictionary. The file stays on the machine | Recognising untitled pages needs them |
| B4 template screens | Several buttons of one screen opening the same screen (FAQ articles, order details) make it a *template*: a name remembers the tap that opened it (`via`), `goto` must end with that tap, and is never "already there" on it. A template named without its tap is not reached at all | Every FAQ article read as one screen on a real app |
| B4 phrases | Phrases with 6+ digits (an account or phone number; years are fine), `@` or an amount are never stored (`map name` refuses them) | A phrase is stored as plain text |
| B4 runs | Within a run, a screen read again after its feed changed is the same screen (matched before learning) | A feed refresh made the start screen look new and take a name |
| B7 arrival | A step arrives when the read is the target, or fits it (no conflicting title, same frame, most of the shorter menu) and does not fit the screen it left: a page held twice (with and without its title) still counts | A real Contact tab was stored twice and goto reported a correct arrival as wrong |
| B4 templates | A template is a screen reached from one screen by buttons that *say* different things; one button learned by its text and by its id is one way in | — |
| B7 fail fast | A hop fails when *any* other screen holds for 1.5 s; the `nav` event is written at the end only; `wait --or` exit 4 outside `goto` is not used as failure evidence | Simpler; fail-safe |
| B9 screens | A screen titled confirm / review and pay / checkout / enter PIN / authorise / verify payment (or a `TNAZAR_UNSAFE_WORDS` word) is never a target; a bare "Pay" or "Transfer" title is a hub, left to the step labels (a "Pay"/"Transfer" button is never tapped). That flag is decided when the screen is read and stored with it (titles are hashed), so unlike step labels it is not recomputed on load | — |
| B13 report | Built: the run report's *Navigation map* section lists every goto and what the run taught | Observability |
| §15.4 devices | One driver per device across homes and machines (an owner marker on the device; `--take-over`); `replay` respects it | Found when a smoke script started on a device another run was driving |
| `TNAZAR_MAP=off` | Outputs identical except `start` prints `map: off` | Disclosure |
| Not built yet | L5 benchmark (`scripts/bench_navmap.py`, needs real agent turns), Phase 0.5 prompt-only experiment, `observe --diff/--hints` (A4, v1.1), team sharing (v1.1), accepting a failure as "not an issue" | — |
