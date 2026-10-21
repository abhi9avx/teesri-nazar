# PRD — Coverage ledger: every requested case and every expected result is accounted for

| | |
|---|---|
| Status | **Draft v0.2**: design grilled and agreed 2026-10-21 (Q1–Q13, all accepted); independent review 2026-10-21 (3 HIGH, 9 MED, 9 LOW) folded in as R1–R7 (§10). Not built. |
| Branch | `feature/coverage`, stacked on `feature/site-trust` |
| Date | 2026-10-21 |
| Technical design | [trd-coverage.md](trd-coverage.md) |
| Test plan | [test-plan-coverage.md](test-plan-coverage.md) |
| Roadmap | `product/bets.md` → Now #8 (and the expectation half of the external review's item 1) |
| Related | ARCHITECTURE.md §11 (pass/fail and the report), §15.1 (plans), *Dashboard acceptance list*; `teesri_nazar/runner.py`, `judge.py`, `report.py`, `replay.py` |

---

## 1. Context gate

- **Strategy context / ICP** [RESOLVED FROM WORKSPACE: product/strategy.md (Last updated:
  2026-10-19)]: QA engineers and SDETs at 10–200 person Android teams who run regression by hand.
  Focus: **Engagement**, meaning people outside the project finish real runs on their own app *and
  trust the result*. No strategic contradiction: this is a trust feature.
- **Evidence base: [STRATEGY-DRIVEN ONLY - NO COMPELLING EVIDENCE]** from customers (no outside
  user yet), but the defect is a **[FACT, code]**, not an assumption:
  - F1 `runner.py:91-95`: an unexpected error ends the case blocked, stops the run and returns;
    later cases are never recorded.
  - F2 `runner.py` `--stop-on-fail`: `break`; later cases are never recorded.
  - F3 a dashboard message hands the run back (exit 3); if the agent then `stop`s instead of
    `run --from`, the remaining cases are never recorded.
  - F4 hand-driven runs record only the cases that were started; the agent's planned list lives
    in chat. Testing 5 of 8 planned cases reports "5 case(s): ✅ 5 pass".
  - F5 the summary line has no denominator.
  - F6 a case needs at least one `check`, but nothing ties checks to the expected results a case
    was written to prove.
  - External PM review (2026-10-21) ranked this the top priority.
- **Stage and constraints:** pre-launch open source, one part-time builder. Appetite: **5 working
  days**. Rules kept: standard library only, Python 3.9+, one driver per device, append-only event
  log, existing exit-code meanings (ARCHITECTURE.md §11).

## 2. School

**Shape Up pitch.** Fixed appetite (5 days), scope flexes. The problem is concrete and found in the
code; there is no outcome to discover first.

## 3. Problem

A QA lead hands Teesri Nazar 20 cases. Today the report can show 7 rows, all green, and nothing on
the page says 13 never ran (F1–F5). And a case written to prove three things can pass after proving
one (F6). A tester who notices this once stops trusting every report after it. For a tool whose
pitch is "hand off your regression", **a false green is the worst possible bug**: worse than a
crash, because nobody looks at it.

## 4. Is it needed? Does it only add?

**Needed: yes, before any outside pilot.** The North Star counts *trustworthy* outside runs; a run
that can silently drop cases is not one. It is also a precondition for regression suites (Next):
comparing builds is meaningless if a case can vanish from one of them.

**Only adds, never takes away** (the rule for this batch):

| What exists today | After this batch |
|---|---|
| Hand-driven runs that never call `case plan` | **Byte-identical** summary line, report and exit codes, plus one report line: "requested cases not declared: completeness unknown" |
| Plan runs (`tnazar run`), which always know their cases | Today's summary line stays **as the prefix, unchanged**; only additions: `○ k not run` among the counts when a case never started, and `  \| verified V/N` at the end. The report adds a Result line; expected results only for cases that use `expect` (R1) |
| Plan syntax, CLI commands and flags | All still valid; new syntax is optional (`expect`, `--for`, `case plan`) |
| Exit codes | Same meanings; `tnazar run` already exits non-zero on F1/F2 |
| Old runs on disk | Open, report and replay as today |
| Dashboard | Same timeline; adds grey "not run" rows and a richer summary only when cases were declared |

Nothing becomes stricter for a user who does not opt in. Everything becomes more honest for one who
does, and the agent (CLAUDE.md, `test-feature`) always opts in.

## 5. Goals and non-goals

**Goals**

1. Every requested case appears in the report exactly once: pass, fail, blocked, skipped or
   **not run**, each non-pass with a reason.
2. The summary states the denominator: `20 case(s): … | verified 18/20`.
3. A run is green only when every requested case passed. An incomplete run never looks green.
4. A case can list its expected results; each must be proven by a check, and the report shows
   which check proved which expected result, and which expected result nothing proved.
5. Replay never turns an unverifiable expected result (DB, Figma) green.

**Non-goals (this batch)**

- Importing test cases from CSV/Markdown files (Q1: `bets.md` Next, interview-gated).
- Preconditions and test-data checks (separate bet: pre-run state check).
- Build-to-build comparison (separate bet: regression suites).
- Any LLM judgement of coverage: every rule here is deterministic.
- Changing how a check matches (exact text, label, selectors).

## 6. Solution (user-visible)

### 6.1 Words (go into GLOSSARY.md)

| Word | Meaning |
|---|---|
| **requested case** | a case the run was asked to execute: the selected cases of a plan, or a case declared with `case plan`, or one started during a run that has a declared list |
| **not run** | requested, never started. Always has a reason. |
| **blocked** | started, could not finish or could not be verified |
| **skipped** | the tester decided it is out of scope (unchanged) |
| **pending** | requested, not started yet, run still going (live view only) |
| **verified** | pass + fail: cases with a definite answer |
| **expected result** (`E1`, `E2`…) | one outcome a case must prove, numbered by order inside its case |

### 6.2 Plans (Q2, Q7, Q8, Q10)

```
case TC-04 "Pay with saved card"
  expect "Confirmation screen shows the amount"
  expect "Card ending 4242 is listed"
  launch --restart
  run open_wallet
  check "•••• 4242" --for E2
  tap "Pay"
  check "₹500" --for E1
  check id=loader --gone
```

The first `expect` is E1, the second E2. The last check has no `--for`: it can fail the case but
proves no expected result. (Plan lines take no trailing `#` comments: R5.)

- `expect` is optional per case and must come before the case's steps.
- When a case has `expect` lines, every `E` needs at least one `check --for` in that case's own
  body, and every `--for` must name an existing `E`; otherwise the plan is refused before anything
  runs (exit 2, naming the line). Flows cannot carry `--for` (their checks are shared by cases).
- A case without `expect` behaves exactly as today.

### 6.3 Hand-driven runs (Q2, Q12)

```
./tnazar case plan TC-01 "Login with valid MPIN" --expect "Home shows the user's name"
./tnazar case plan TC-02 "Wrong MPIN shows an error" --expect "Error text shown" --expect "Stays on MPIN"
   → TC-02 planned: E1 Error text shown; E2 Stays on MPIN
./tnazar case start TC-02 …  ./tnazar check "Incorrect MPIN" --for E1 …  ./tnazar case end
```

Re-declaring an ID updates its title and expected results. Starting a case not on a declared list
adds it to the list, marked *added during run*.

### 6.4 Selection (Q3)

Requested = the cases selected for this run. `--only TC-05` requests one case; the report header
says `1 of 12 plan cases selected (--only TC-05)`. `--from TC-07` continuing the same open run adds
to that run's list; it never removes earlier cases.

### 6.5 Results (Q4, Q5, Q9, Q11)

| Situation | Case result | Reason shown |
|---|---|---|
| Never started; the run ended | **not run** | `run stopped at TC-03: error: …` / `--stop-on-fail after TC-03` / `run stopped with tnazar stop before this case started` / `run closed by a new tnazar start` / `not run in the recorded run` (replay) |
| A check linked to E2 failed | **fail** | `failed: …` (as today) |
| All checks that ran passed, E2 had none run (e.g. an `if` branch not taken) | **blocked** | `not verified: E2 Card ending 4242 is listed` |
| Tester ran `case end pass` with E2 unverified | **blocked** | `marked pass by the tester; not verified: E2 …` |
| Replay: E3's only proof was a DB/Figma check | **blocked** | `not verified: E3 …`, next to the existing note `[db] not re-verified on replay: …` |

**Green** needs every requested case passed **and** no failed check anywhere in the run, including
checks recorded outside any case (R6).

`tnazar run` exits 1 when any requested case is not a pass (exit 2 and 3 keep their meanings).
Replay counts not run like blocked: as not passing.

### 6.6 Summary and report (Q5, Q6, Q13)

With declared cases:

```
20 case(s): ✅ 16 pass  ❌ 2 fail  ⛔ 1 blocked  ○ 1 not run  | checks 40/43 passed  | verified 18/20
```

Without declarations (unchanged): `3 case(s): ✅ 3 pass  | checks 4/4 passed`. The declared line
is today's line with additions only, so every tool and test that reads today's line still matches
(R1). During a live run, `⏳ k pending` is added among the counts.

The HTML/Markdown report adds, only for declared runs: a **Result** line (`all passed` / `failed` /
`incomplete`), the selection line, grey not-run rows with their reason, and under each case its
expected results with ✅ / ❌ / ○ and the check that proved each. Undeclared runs get one line:
`requested cases not declared: completeness unknown`.

### 6.7 Dashboard (Q13)

The summary in the header uses the line above. When a run ends, each not-run case is a grey row
with its reason. New acceptance line **DA-14** (*live*): *show every requested case once the run
ends, not-run ones as grey rows with their reason*.

### 6.8 Agent behaviour (CLAUDE.md, `test-feature` skill)

- Workflow step 1: every planned case gets its expected results; step 3: write them as `expect`
  lines and link each `check` with `--for`.
- Hand-driven runs: `case plan` every case from step 1 before the first `case start`.
- Step 5 report: quote the `verified N/M` line; list not-run and not-verified items explicitly.

## 7. Risks and rabbit holes

| Risk | Mitigation |
|---|---|
| Changing the summary line breaks the dashboard or tests | Old format kept byte-identical when nothing is declared; new format only with declarations (Q6) |
| Expected results become busywork and the agent writes one vague `expect` per case | The agent prompt asks for one `expect` per expected result in the user's test case; review in pilots |
| An `if` branch makes a linked check optional, so cases end blocked unexpectedly | That is the intended honest answer (Q9); the reason names the E so the plan can be fixed |
| A case re-run replaces the earlier attempt; its expectation state must follow the latest attempt | Computed from the latest attempt's events only (existing `case_events`) |
| Rabbit hole: matching free-text expectations to checks automatically | Out: linking is explicit with `--for` |
| Rabbit hole: CSV import | Out (Q1) |

## 8. Done when

1. A plan stopped at case 3 of 10 (each of F1, F2, F3) reports 10 rows: 3 with results, 7 not run
   with the right reason; `run` exits non-zero.
2. A hand-driven run that declared 8 cases and started 5 reports `8 case(s): … ○ 3 not run … |
   verified 5/8` and is not green.
3. A plan where an `expect` has no `check --for` is refused before the device is touched.
4. A case whose linked check sat in an untaken `if` branch ends blocked naming the E.
5. A replay of a case whose E was proven only by a DB check ends blocked, not pass.
6. Every existing test passes **unchanged**. The characterisation tests (test plan §3) prove that
   undeclared runs give the same summary line, report rows and exit codes as before, and that plan
   runs keep today's line as an unchanged prefix.
7. The four "done" steps from CLAUDE.md: acceptance line DA-14 mapped to tests, suite green on
   Python 3.9, one `ai-code-review` with HIGH/MED fixed, `scripts/smoke_dashboard.py` passes on a
   device with the new DA-14 step.

## 9. Success measure

- Guardrail (bets.md): **0 false greens**. In the planted-bug benchmark (Now #10), no run that
  skipped or failed to verify anything reports all passed.
- Pilot signal [assumption; revisit after interview 5]: interviewees can say, from the report alone,
  how many of their cases ran and which expected result failed, without asking the founder.

## 10. Decisions log (grilling, 2026-10-21, all accepted)

| # | Decision |
|---|---|
| Q1 | Batch = A (requested list, not run, verified N/M, never green when incomplete) + B (expected results ↔ checks). C (CSV/MD import) stays in Next. |
| Q2 | Requested list: the plan's cases for `run`; `case plan` for hand-driven runs; CLAUDE.md requires it. |
| Q3 | Requested = selection (`--only`/`--from`), merged across `--from` continuations; header shows it. |
| Q4 | New status *not run* with a reason, recorded when the run finishes. |
| Q5 | Verified = pass + fail; green = all requested pass; `run` exits 1 on any non-pass; replay counts not run as failing. |
| Q6 | No declarations → today's output exactly, plus "completeness unknown" line. |
| Q7 | `expect` lines numbered E1… by order; `check --for E1[,E2]`; same in hand-driven runs and agent-verified checks. |
| Q8 | `expect` optional; when used, the parser requires a linked check per E and valid `--for` IDs. |
| Q9 | An E with no linked check run → case blocked "not verified: E…"; a failed linked check → fail. |
| Q10 | Checks without `--for` are allowed as extra checks. |
| Q11 | Replay: an E proven only by DB/Figma → "not re-verified" → blocked. Only for cases with `expect`. |
| Q12 | Undeclared `case start` is accepted and marked *added during run*; re-declaring updates. |
| Q13 | Report shows verified N/M, grey not-run rows, per-case expected results; dashboard grey rows; DA-14 live. |

**Review refinements (2026-10-21)**, same intent as Q1–Q13, found by the independent review:

| # | Refinement | Why |
|---|---|---|
| R1 | The declared summary line is today's line plus additions (`○ k not run`, `\| verified V/N`), not a new format | Plan runs are always declared; a new format would change every plan user's output and break `runner._stop()`'s filter and `test_plan.py:193` |
| R2 | `case_plan` and `not_run` are narration for replay and hidden rows for the dashboard timeline | Replay would otherwise treat them as device steps (0.3 s each, shifted step numbers); the dashboard would show "• case_plan" rows |
| R3 | Closing the open case and writing `not_run` happen before device cleanup in `finish_run` and in replay's `finally` | An adb error must never leave a case "running" or skip not-run rows |
| R4 | "not verified: E…" is always added to the reason, even when a reason was passed | An auto-ended case ("next case started…") must still name the unverified E |
| R5 | No trailing `#` comments in plan examples | The parser treats them as arguments; `#12` targets forbid changing that |
| R6 | Green also needs zero failed checks outside cases | A failed `--visual` check (case-less) must not sit under "all passed" |
| R7 | Cut: `status` line, `source` field beyond `added`, `reason_hint`, `.pill.not_run`, the timing test | Not needed for the goal |
