# Changelog

What changed in each version of Teesri Nazar, newest first. The website's changelog pages
are built from this file, so every version shipped here shows up there too.

## v4: Easy to set up, honest to trust

In progress since 21 October 2026: [PR #7](https://github.com/abhi9avx/teesri-nazar/pull/7) and a website update

v4 is about people outside the project installing Teesri Nazar, trusting what it says and
finishing a run on their own app.

- `tnazar doctor` checks the setup in one command: adb, each connected device (ready,
  unauthorised, offline or still booting), Python, the screen size and fast screen reads.
  It prints the fix for every problem and changes nothing.
- A device that is not ready now says why (unauthorised, offline) and how to fix it,
  instead of "no device".
- Every requested case is in the report. A plan run that stops early (an error,
  `--stop-on-fail`) lists the cases after it as *not run*, with why, and the summary ends
  with `verified N/M`; an unfinished run never reads as all passed. By hand, `case plan`
  declares the cases first.
- Cases can list their expected results (`expect` in a plan, `case plan --expect`), and a
  check says which one it proves (`check --for E1`). An expected result nothing proved
  blocks its case, by name, instead of passing; on replay, one proven only in the database
  is not re-verified, so its case is not green.
- `tnazar cases import` brings test cases from a sheet (CSV, a table pasted from a
  spreadsheet, or a Markdown table) into a draft plan, keeping their IDs, sections and
  accounts; `tnazar plan review` shows every expected result and what proves it, and all
  that is still missing, before a run.
- Requirements: give it a spec and the plan lists what the feature must do
  (`requirement R1 "…" --source "§2.4"`), each expected result says which one it proves
  (`expect "…" --req R1`), and you approve the list with `tnazar plan approve`. The report
  says `requirements 4/5 verified` and names any requirement nothing tested, so six passing
  cases never read as a tested feature. A plan with requirements exits 1 until every one in
  scope is verified; plans without them work as before.
- Only evidence about the app fails a case. A step that could not run, a device that
  stopped answering or an expected result never proven blocks the case and says why;
  `require` in a plan names the state the setup must reach, and when it does not hold the
  case is blocked as *precondition not met*, never failed.
- A shared step that keeps failing (such as login) no longer makes every later case time
  out as failed: after two failures in a row, the cases needing it are blocked at once,
  with the reason.
- `tnazar observe --full` prints long texts whole, for chat replies and articles.
- The website's hero animation is labelled as an illustration, with a link to a report
  from a real run.
- New docs page, *Data and privacy*: what stays on your computer, what Claude Code sends
  to Anthropic, and how secrets are kept out.
- *Limits and fixes* opens with a table of what works today, partly, and not yet.
- The quick start runs `tnazar doctor` before the first test.
- The website fits small phones: a two-row header, the hero's checks as a grid, one
  scrolling row for the docs menu, and reports that fit a 320px screen.
- The README's "Coming next" (parallel emulators, DB checks, Zephyr) is gone: none of it
  is being built now, so it is no longer promised.

## v3: It learns your app

Released 19 October 2026 in [PR #4](https://github.com/abhi9avx/teesri-nazar/pull/4)

Every run now teaches Teesri Nazar the app's screens and the taps between them. The next
time you ask for a screen, it goes straight there.

- `tnazar goto "wi-fi"` reaches a screen in one command by a route learned in earlier
  runs, verifying every step. A miss prints the screen it is on, so exploring costs nothing
  extra.
- `tnazar map` shows what was learned: screens, their names and the steps that can be
  replayed. `map name` and `map forget` fix a wrong name.
- Screens are told apart by their title, their menu and their layout, so look-alike list
  pages, tab strips and pages without a title stay separate.
- Loading pages, wrong turns and taps that did nothing are never learned as routes.
- Money-moving steps (pay, transfer, confirm, PIN screens) are never replayed by `goto`.
- One driver per device, even across terminals and machines.
- The report shows the navigation map, and the dashboard shows `goto` steps.

Also launched in October 2026: the website and docs at
[teesrinazar.github.io](https://teesrinazar.github.io), with guides, a sample report and
this changelog.

## v2: Steer it live

Released 19 October 2026 in [PR #2](https://github.com/abhi9avx/teesri-nazar/pull/2) and
[PR #3](https://github.com/abhi9avx/teesri-nazar/pull/3)

The live dashboard became a real control panel: you see what the agent is doing and it
hears you within a second.

- A status line says what the agent is doing: got your message, thinking, working on a
  step, listening.
- `tnazar wait-message` lets the agent hear a dashboard message while it is idle or busy.
- *Type it now*: the dashboard types an OTP or PIN into the focused field itself, because
  those screens expire. The value never reaches the chat or the disk.
- App flows: save a route once in a plan file and run it with `tnazar flow <name>`.
- One device lock, so two runs never fight over a phone.
- adb output that is not valid UTF-8 no longer crashes a run.
- The timeline stays in time order, and failed steps show as red rows with the reason.

## v1: The tester

Built 16 to 18 October 2026: the initial commits, then
[PR #1](https://github.com/abhi9avx/teesri-nazar/pull/1)

The first working AI tester: tell Claude Code what to test, and it plans the cases, taps
through the app on a real phone and hands you a pass/fail report.

- Look, act, check, record: `observe`, `tap`, `type`, `scroll`, `check`, by id, label or text.
- Pass/fail per test case with a screenshot for every check, in a Markdown and HTML report.
- `tnazar replay` runs a recorded test again, on the same or another device.
- Plan files: `tnazar run plans/x.plan` runs every case in one go, with no AI turn per step.
  Plans work on any screen size.
- Fast screen reads: ~0.05 s through the UiAutomator2 server instead of ~2 s.
- The live dashboard: watch the screen, follow the timeline, pause, send a message.
- Secrets typed from `.env` with `--env`, never printed or recorded.
- Slow steps, crashes and ANRs are flagged automatically, and each case shows its token
  cost.
- Unit tests on a fake device and CI on Python 3.9 and 3.13.
