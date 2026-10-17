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
2. **Device.** `./tnazar devices`. One device → use it. Several → ask which one.
   None → stop and tell the user.
3. **Plan.** Numbered cases `TC-NN` with steps, expected result and verification type
   (ui / db / figma), plus added edge cases and an explicit "not testing" list.
   Wait for approval unless the user said to go ahead.
4. **Run.** Explore the screens the cases touch (`observe`), write `plans/<name>.plan`
   (format: `teesri_nazar/plan.py`, example `examples/settings.plan`), then
   `./tnazar run plans/<name>.plan` in the background. Exit 1 = a case failed, 2 = plan
   or setup error, 3 = the user sent a dashboard message: act on it, then `--from <case>`
   (all codes: `ARCHITECTURE.md` §11). Re-run one case with `--only`. Keep going after a failure; a failed
   case doesn't stop the run. If a precondition breaks (login, backend down), mark the
   dependent cases `blocked` with the reason.
5. **Finish.** `./tnazar stop`, then report in chat: summary line, case table
   (id, title, result, reason), each failure with expected vs actual and the screenshot
   path, bugs found, what was not tested, and the `report.html` path.
