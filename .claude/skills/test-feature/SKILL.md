---
name: test-feature
description: Test a new Android app feature end to end with tnazar — read the inputs (free text, pasted test cases, PRD, Figma, Zephyr/Jira, DB details), plan test cases including edge cases, execute them on the connected device with UI/DB/Figma checks, and report pass/fail with evidence. Use when the user asks to test, verify or QA a feature, flow or screen on Android, or shares test cases to run.
---

# Test a feature

Follow `CLAUDE.md` (the playbook). In short:

1. **Gather.** Read every input the user gave. Open links with the matching tool:
   Confluence/Jira via the Atlassian MCP, Figma via the Figma MCP. Read `testdata.yaml`
   if it exists. If something essential is missing (which app, which account, which
   environment), ask once, briefly.
2. **Device.** `./tnazar doctor`: it checks adb, the devices, Python and fast screen reads,
   and prints the fix for each problem. Exit 2 → follow its `fix:` lines (or tell the user
   the ones only they can do, such as accepting the USB debugging prompt). One ready device →
   use it. Several → ask which one.
3. **Plan.** Numbered cases `TC-NN` with steps, expected results (E1, E2…: one per outcome
   the case must prove) and verification type (ui / db / figma), plus added edge cases and an
   explicit "not testing" list.
   Wait for approval unless the user said to go ahead.
4. **Run.** Check the app map first (`./tnazar flows`; `./tnazar flow <name>` runs a saved
   route). Explore the screens the cases touch (`observe`), save new routes as `flow`s, write `plans/<name>.plan`
   (format: `teesri_nazar/plan.py`, example `examples/settings.plan`; each expected result
   is an `expect` line proven by a `check ... --for E<n>`), then
   `./tnazar run plans/<name>.plan` in the background. Exit 1 = a case failed, 2 = plan
   or setup error, 3 = the user sent a dashboard message: act on it, then `--from <case>`
   (all codes: `ARCHITECTURE.md` §11). Re-run one case with `--only`. Keep going after a failure; a failed
   case doesn't stop the run. If a precondition breaks (login, backend down), mark the
   dependent cases `blocked` with the reason.
   Driving by hand instead: `./tnazar case plan TC-NN "title" --expect …` for every planned
   case first, so a case that never runs is still reported.
5. **Finish.** `./tnazar stop`, then report in chat: summary line with `verified N/M`, case table
   of every requested case
   (id, title, result, reason), each failure with expected vs actual and the screenshot
   path, bugs found, what was not tested, and the `report.html` path.
