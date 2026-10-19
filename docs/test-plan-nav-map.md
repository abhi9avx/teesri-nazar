# Test plan — Faster screen reading and a self-learning navigation map

| | |
|---|---|
| Status | **Draft v0.2** — revised with the PRD after three reviews; for founder review |
| PRD | [prd-nav-map.md](prd-nav-map.md) (section refs below are PRD sections) |
| Branch | `feature/nav-map` |
| Rule | **Test first.** Each behaviour gets a failing test before its code. Phase 0 tests pass on today's code *before* any change and stay green after every phase. |

---

## 1. Strategy

| Level | Proves | Where | When |
|---|---|---|---|
| **L1 Pure** | `identity()`, phrase normalising, `learn(events, screens)`, routing, scrub, unsafe detection, fold of the store | `tests/test_identity.py`, `tests/test_navmap.py` | every commit, CI (Python 3.9) |
| **L2 CLI in-process** | commands against the fake device: stdout, exit codes, `events.jsonl`, adb calls, screen reads | `tests/test_goto.py`, `tests/test_map_cli.py`, `tests/test_cli.py` | every commit, CI |
| **L3 Acceptance** | DA-13 mapped to tests (`ui.html` pure functions under node) | `tests/test_acceptance.py` | every commit, CI |
| **L4 Device smoke** | emulator, public apps, map off vs on | `scripts/smoke_navmap.py --device <serial>` | before any push |
| **L5 Benchmark** | intents × (map off, cold, warm): turns, time, tokens, success, wrong-screen | `scripts/bench_navmap.py` | Phase 0 (baseline) and Phase 3 (gate) |

**Seams tests may touch:** the CLI (`tnazar()` in-process), pure functions over XML/event fixtures, and
the map **through `tnazar map [--edges]` / `goto --dry-run`** or the `NavMap` module — never by parsing
`map.jsonl`, so the format can change.

**Test isolation (guard test G-01):** the `fake_device` helper sets `TNAZAR_MAP_DIR` and the machine id
to a temp dir; a test fails if anything is written under the real `~/.tnazar/maps`.

**Clock:** the map module takes an injectable clock (FakeClock today patches only device, replay,
screen and uia2) — needed for the 60 s cap and episode timing.

**Python 3.9:** no `X | None` outside annotations, `match`, `itertools.pairwise`, `zip(strict=)`,
`bisect(key=)`, dataclass `slots`. CI on 3.9.6 is the gate; run `/usr/bin/python3` locally.

## 2. Fixtures and fake device

### 2.1 Screen shapes — `tests/fixtures/shapes/*.xml`

Dumped from public apps on an emulator (AOSP Settings, Clock, Files, a Compose sample, a Flutter
sample, a WebView sample), then scrubbed.

| Fixture | Shape |
|---|---|
| `bottom_nav.xml` | bottom tabs, ids present |
| `drawer_closed.xml`, `drawer_open.xml` | hamburger menu |
| `compose_no_ids.xml`, `compose_counter_a.xml`, `compose_counter_b.xml` | Compose, no ids; same screen with a changed number |
| `list_rows_20.xml`, `list_rows_35.xml` | same list screen, different row counts and contents |
| `two_titles.xml` pair | two screens with near-identical chrome but different titles |
| `webview.xml`, `flutter_no_semantics.xml`, `flutter_semantics.xml` | opaque and not |
| `loading.xml` | spinner only |
| `dialog_over_screen.xml`, `bottom_sheet.xml`, `keyboard_open.xml` | overlays, two packages |
| `password_form.xml` | password + OTP fields |
| `uia2_variant.xml` | same screen as the UiAutomator2 server returns it |

**Hygiene (test FX-01 `test_fixtures_are_scrubbed`):** packages `com.example.*` only; text from a fixed
fake vocabulary; rejects 6+ digit runs, `@`, currency amounts outside a fixed set, and words from a
deny-list in an **untracked** local file. Never copy from `.tnazar/`, `runs/` or `plans/`.

### 2.2 `tests/fakedevice.py` changes (test code only)

1. Package per screen and per node (dialog over screen, keyboard, launcher).
2. Serve raw XML screens, hit-tested with `screen.parse`.
3. `on_tap` keyed by id, text **and** desc (Compose apps without ids).
4. Stateful transitions (callable or list) — e.g. a dialog only on first open.
5. A back stack and launcher model; `on_url` for `open-url` (no-op today).
6. A `becomes` chain through an intermediate screen (loading → target).
7. A `reads()` helper counting both the dump path and the UiAutomator2 path.
8. Version swap between runs (`adb.version`) for the v2 build.

### 2.3 Fake apps

- **`bank_app`** — Home ⇄ Savings → Passbook; Home → Profile → Statements → Passbook (two entry points);
  Home → Loans → Active loan → Loan statement; Home → Pay → Confirm (unsafe); a loading screen between
  Loans and Active loan; a "Hi <name>" greeting on Home.
- **`bank_app_v2`** — same, "Savings" renamed "Accounts", version bumped.
- **`shop_app`** — Compose-style, no ids: Home → Orders → Order detail; Home → Search (typed) → Results.

## 3. Phase 0 — characterisation (green on today's code)

| ID | Test | Pins |
|---|---|---|
| C-01 | exact `observe` stdout for `login.xml` and every shape fixture, with/without `--all`; element count, `interesting()` set and `stable_selector` per node | default output and parser |
| C-02 | an unlabelled clickable icon prints `[n] ImageView @x,y tap` | the line A4 must not change by default |
| C-03 | `#n` counts positions in the full element list | `#index` meaning |
| C-04 | **reads per command**, both readers: `tap <sel>` = 1 (before acting), `tap '#n'` / `tap x,y` = 0, `type --into` = 1, `wait X` = polls, `check X` = polls, `check --gone` = polls, `scroll --to` = 1 + 1 per swipe, `key`/`launch`/`open-url` = 0, `flow` = sum of steps | the B2 read table |
| C-05 | `last-screen.xml` written by `observe`, reading `scroll`, `tap`/`type --into --wait 0`; **not** by plain `tap X`, `type --into X`, `wait`, `check` | PRD A3 (corrected) |
| C-06 | scrolling to the end stops when the screen stops changing, including when only text changes | `signature()` behaviour |
| C-07 | replay and the dashboard timeline ignore an unknown event type | new events can't break old runs/UI |
| C-08 | exit codes 0–5 for existing commands (table-driven) | ARCHITECTURE §11 |
| C-09 | **map-off vs map-on harness**: a scripted session's adb calls and read counts recorded for later diffing (REC-03) | never-worse baseline |

## 4. Part A — identity

| ID | Test | Expect | Fixture |
|---|---|---|---|
| ID-01 | dynamic numbers ignored | counter/balance change → same id | `compose_counter_a/b` |
| ID-02 | different screens differ | login ≠ home ≠ passbook | `bank_app` |
| ID-03 | Compose without ids | two tabs differ | `compose_no_ids` |
| ID-04 | list rows ignored | 20 vs 35 rows, different contents → same id; no row text in tokens | `list_rows_*` |
| ID-05 | near-duplicate | one badge added → same; half the tokens → different | synthetic |
| ID-06 | **titles must match** | near-identical chrome, different titles → different | `two_titles` |
| ID-07 | not transitive | A~B, B~C, A≁C stored in either order → result depends only on stored canonicals, never drifts | synthetic chain |
| ID-08 | package = majority of interactive nodes | keyboard first → app package | `keyboard_open` |
| ID-09 | kinds | WebView/Flutter → `opaque`; spinner → `loading`; dialog/sheet → `overlay`; Flutter with semantics → normal | shapes |
| ID-10 | secrets and inputs never in tokens | typed OTP/password/EditText values absent | `password_form` + `.env` |
| ID-11 | same screen through both readers | dump and UiAutomator2 XML → same id | `uia2_variant` |
| ID-12 | tokens hashed; plain text only for selectors and title | inspect via `map audit` | `bank_app` |

## 5. Part B — learning map

### LEARN — when to record (PRD B2). L1 tests on `learn(events, screens)`, plus L2 end to end.

| ID | Events in the run | Learned |
|---|---|---|
| LRN-01 | observe Home → `tap id=savings` → `wait "Passbook"`… settled on Savings | edge Home→Savings `id=savings` |
| LRN-02 | `tap id=savings` reads Home (before) → next `tap` reads Savings | edge from the second tap's pre-read |
| LRN-03 | tap, then next settled read is the same screen | nothing (toggle/no-op) |
| LRN-04 | two taps before the next settled read | nothing |
| LRN-05 | loading / opaque read between | transparent → edge to the next real screen |
| LRN-06 | `tap x,y` / `tap 50%,90%` | knowledge-only edge, not runnable |
| LRN-07 | `tap '#n'` where the element has an id | edge with the resolved selector |
| LRN-08 | `type` / dashboard "type it now" between | nothing |
| LRN-09 | `key back`, "Navigate up" | knowledge-only |
| LRN-10 | `launch --fresh` then settled on Home | edge `@cold`→Home |
| LRN-11 | plain `launch` / `open-url` | chain broken; no edge |
| LRN-12 | 61 s between action and next settled read | nothing |
| LRN-13 | dialog appears between | X→overlay, overlay→Y; overlay is a screen |
| LRN-14 | `wait A --or B` exit 4 | failure count on the edge expected to reach A |
| LRN-15 | `tnazar run <plan>` steps | learned like hand-driven |
| LRN-16 | `replay` | nothing learned (v1) |
| LRN-17 | intermediate polls of a `wait` that saw a half-drawn screen | only the settled read counts |
| LRN-18 | unstopped run → next `start` | learned at `start`; nothing crosses into the new run |

### REC — end to end (L2)

| ID | Test | Expect |
|---|---|---|
| REC-01 | hand-driven Home → Savings → Passbook, then `map --edges` | 3 screens, 2 edges |
| REC-02 | **`goto` learns on demand**: route found earlier in the same run | `goto` uses it without `stop` |
| REC-03 | **zero extra reads/calls**: C-09 session map on vs off | identical adb calls and reads |
| REC-04 | recording overhead | ≤ 20 ms per command (fake clock + timer, generous bound) |
| REC-05 | `TNAZAR_MAP=off` | nothing written; outputs identical; `start` prints `map: off` |
| REC-06 | disclosure | `start` prints `map: learning routes into …/<package>` |
| REC-07 | map dir unwritable / is a file (injected) | one warning; command result and exit code unchanged |
| REC-08 | `observe` hidden event | not shown in the dashboard timeline, skipped by replay |

### DUP — no duplicates (PRD B3)

| ID | Test | Expect |
|---|---|---|
| DUP-01 | Passbook visited 10× in one run; 50 identical taps | one screen, one edge; distinct runs = 1 |
| DUP-02 | same route in 3 runs | one edge; distinct runs = 3 |
| DUP-03 | "Open my Passbook", "go to passbook", "PASSBOOK" | one name |
| DUP-04 | near-duplicate screens | one canonical |
| DUP-05 | 100 repeat runs, then `map compact` | `map` output unchanged; file size under a pinned bound |

### NAME — names (PRD B4)

| ID | Test | Expect |
|---|---|---|
| NAME-01 | `goto passbook` (unknown, exit 2) → explore → `check "Passbook"` → `case end` | bound; next `goto passbook` arrives |
| NAME-02 | episode with no passing selector check | nothing bound |
| NAME-03 | **last** check wins: `check "Active loan"` then `check "Loan statement"` in episode "loan statement" | bound to Loan statement |
| NAME-04 | `check --gone`, `check --result`, check on the start screen | never bind |
| NAME-05 | episode closes at next `goto`, `case start/end`, `launch --restart`, `stop`, 25 actions | a later unrelated check does not bind |
| NAME-06 | `map name here passbook --alias statement` | both phrases → screen |
| NAME-07 | phrase bound to two screens | `goto` exit 2 listing both |
| NAME-08 | `say` text, case titles | never bind |
| NAME-09 | seeding: `flow open_settings` in `plans/x.plan` for the package | `goto settings` runs the flow |
| NAME-10 | multi-step (S7): `goto active loan`, explore one step, check, close | "loan statement" runs the full path next time |
| NAME-11 | no substring matching | `goto statement` with only "loan statement" known → exit 2 (miss), not a guess |

### ROUTE — from where the user is (PRD B5)

| ID | Test | Expect |
|---|---|---|
| ROUTE-01 | from Home, 2-hop and 3-hop routes to Passbook | 2-hop |
| ROUTE-02 | from Profile | Profile → Statements → Passbook |
| ROUTE-03 | already on Passbook | exit 0, 0 taps, 1 read |
| ROUTE-04 | start screen unknown | exit 2; prints the screen like `observe` (no extra read) and known names; **no back presses** |
| ROUTE-05 | current screen, not a stale `observe` | correct start |
| ROUTE-06 | `goto <screen-id>` | same as by name |
| ROUTE-07 | hand-written flow with the same name | flow runs |
| ROUTE-08 | only route passes an unsafe / back / xy / type edge | exit 2 "no runnable route", route shown in `--dry-run` |

### GOTO — fast, verified execution (PRD B6–B7)

| ID | Test | Expect |
|---|---|---|
| GOTO-01 | 3-hop route | one CLI call; 3 taps; reads = 1 + polls; ≤ the same route as a hand-written flow; exit 0 |
| GOTO-02 | **false arrival guard**: the tapped label equals the next screen's title, and the tap does nothing | hop fails (identity unchanged), never exit 0 |
| GOTO-03 | lands on another known screen, stable 2 reads | fails fast, before timeout |
| GOTO-04 | loading screen mid-hop | waits through it, arrives |
| GOTO-05 | dashboard message during hop 2 | exit 3 between hops |
| GOTO-06 | paused | exit 5 |
| GOTO-07 | **no deadlock**: registered `device=True, action=False`; inner taps take the lock per hop | completes; no `DeviceBusy` |
| GOTO-08 | inner exit 4 | mapped to 1 |
| GOTO-09 | events: each hop a normal `tap` event + `nav` start/end; replay of the run is green | faithful replay |
| GOTO-10 | `goto` in a `.plan` file | plan error, exit 2 |
| GOTO-11 | **a miss costs nothing extra**: unknown phrase | exit 2, prints the screen listing from its one read; total reads = 1 (same as one `observe`) |
| GOTO-12 | `--dry-run` | prints hops, counts, reason; 1 read, 0 taps |
| GOTO-13 | no run / map off | exit 2 with a one-line reason |
| GOTO-14 | route learned once in this run (F2 = once) | runs automatically |

### HEAL — mistakes and new builds (PRD B8)

| ID | Test | Expect |
|---|---|---|
| HEAL-01 | `bank_app_v2`, "Savings" renamed | exit 1 naming hop 1, expected vs current screen printed; cost ≤ one hop timeout |
| HEAL-02 | then agent taps "Accounts", reaches Passbook, checks | new edge; next `goto` uses it, never the old one |
| HEAL-03 | failed edge in this run | not retried in the same run |
| HEAL-04 | 2 consecutive failures on the current version | retired; a later success un-retires |
| HEAL-05 | empty `versionName` | treated as one version; no crash |

### SAFE and PRIV (PRD B9)

| ID | Test | Expect |
|---|---|---|
| SAFE-01 | edge into Pay / Confirm | stored, never run |
| SAFE-02 | edited file marks it safe | still unsafe (recomputed on load) |
| SAFE-03 | target with password/MPIN field | unsafe |
| SAFE-04 | `map_unsafe_words` in `testdata.yaml` (e.g. a local-language word) | applied |
| PRIV-01 | run shows "Hi <name>", a balance, list rows, types an OTP | none of those strings in any stored byte |
| PRIV-02 | labels > 24 chars, emails, phones, amounts | dropped |
| PRIV-03 | `map audit` | lists exactly the plain-text strings stored |

### STORE (PRD B10)

| ID | Test | Expect |
|---|---|---|
| STORE-01 | location | under `TNAZAR_MAP_DIR/<package>/`; nothing inside the repo |
| STORE-02 | app isolation | app A never shown for app B; wrong-package lines dropped |
| STORE-03 | two processes × 500 appends | no lost or torn lines |
| STORE-04 | torn last line, then append | repaired; both lines readable |
| STORE-05 | compaction with `os.replace` raising (injected) | old file intact; warning |
| STORE-06 | `map forget` | gone; new evidence can re-learn |
| STORE-07 | corrupt line / unknown schema | warning, run continues, exit codes unchanged |
| STORE-08 | lock unavailable (injected) | recording skipped, one warning |
| STORE-09 | two runs on two devices, same machine, same app | both learned |

### UI and metrics

| ID | Test | Expect |
|---|---|---|
| UI-01 (DA-13) | goto heading, hop rows, red failed-hop row with expected/actual | node tests on `describe()` / `agentLine()` |
| UI-02 | old run timeline | renders unchanged (golden) |
| MET-01 | `nav` events | phrase, route, hops, outcome, duration present and correct |

## 6. L4 — device smoke (`scripts/smoke_navmap.py`)

Emulator, public apps only (Android Settings, Clock, Files, one Compose sample):

1. Map off: a 3-screen task by hand commands — record reads, time, outputs.
2. Map on, run 1: same task — outputs and reads identical to step 1; `map` lists the screens.
3. `goto <target>` from the start screen — arrives in one call; time recorded.
4. `goto` from a different screen (second entry point).
5. Same phrase 5× — `map` shows no duplicates.
6. Break a route (`map forget` a screen's edge, or a settings change that hides an entry) — `goto`
   stops at the hop with a clear message; explore; `goto` works again.
7. `map audit` — no device name, account or typed value.
8. Dashboard checked live: heading, hop rows, red failed hop.

Customer apps are tested **locally only**, with plans and maps outside this repo; nothing from them is
committed.

## 7. L5 — benchmark (`scripts/bench_navmap.py`)

- **Intents:** ~30 across Settings, Clock, Files, a Compose sample and a shop-like sample ("open
  battery", "open alarms", "open downloads", "open orders"), each its own case with a ground-truth
  `check`.
- **Per intent:** turns, wall time, tokens (`usage.per_case`), success, **wrong-screen** (goto exit 0
  but ground-truth check fails).
- **Runs:** map off (Phase 0 baseline and navigation share), map on cold, map on warm (2nd and 3rd run).
- **Merge gate:** warm ≤ off on turns and time for every intent; success never below map off;
  wrong-screen = 0; kill criteria (PRD §9) evaluated.
- Phase 0.5 (prompt-only `flow` saving) runs the same suite to compare.

## 8. Budgets (pinned)

| Budget | Value |
|---|---|
| Extra screen reads / adb calls from recording | 0 |
| Recording overhead | ≤ 20 ms per command |
| `goto` miss | 1 read total |
| `goto` hit | 1 + polls per hop; ≤ same route as a hand-written flow |
| Failed hop | ≤ one hop timeout |
| Map load (500 screens / 5,000 edges) | measured in the benchmark, not a CI unit test |

## 9. Exit criteria (before any push or PR)

1. Phase 0 tests green on today's code and after every phase.
2. Full suite green on `/usr/bin/python3` (3.9).
3. One AI review (`ai-code-review` against `main`); HIGH/MED fixed; LOW listed.
4. Device smoke green; dashboard checked live.
5. Benchmark gate met (Phase 3).
6. FX-01 scrub test green; no customer content in the diff or commit messages.
