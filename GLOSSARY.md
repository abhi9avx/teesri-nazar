# Glossary

## Device setup

**Ready device**: a device that adb lists in the `device` state and that has finished booting,
so commands can drive it. A listed device that is *unauthorised*, *offline*, *without
permissions*, *still booting* or *not answering* is not ready.
_Avoid_: connected device (a connected phone may still be unauthorised).

**Blocker**: a failed setup check that stops every run until it is fixed (no adb, no ready
device, Python older than 3.9).
_Avoid_: error, failure (both also describe a failed test case).

**Warning**: a setup check that does not stop a run but makes it worse, such as slow screen
reads because the UiAutomator2 server is missing.

## Test results

**Requested case**: a case a run was asked to execute: a selected case of a plan, a case
declared with `case plan`, or one started during a run that has declared cases.
_Avoid_: planned case (the plan file may hold cases that were not selected).

**Not run**: requested, never started; always has a reason.
_Avoid_: skipped (the tester chose that), blocked (that case started).

**Verified**: pass + fail: the requested cases with a definite answer.

**Draft plan**: a plan written by `cases import`, or one still being written: its cases and
expected results are there, its steps and checks not yet. `run` refuses it; `plan review`
lists what is missing.

**Precondition failed**: a case blocked without running, because a shared flow it needs
(such as `login`) failed in the two cases before. Its cause is *precondition* when the flow
failed on a `require`, else *step*.

**Require**: a plan step naming the state a case's setup must reach before its checks (a
fresh chat, a logged-in home). When it does not hold, the case stops as *precondition not
met*. Never an expected result.
_Avoid_: assertion, check (a check proves an expected result; a require only gates one).

**Cause**: what a result that is not a pass points at, shown after it (`fail · app`):
*app* (a failed check, an error screen named with `--or`, a crash: the only cause of a fail),
*step* (a plan step could not run: the app or the test may be at fault), *device* (adb lost
the device), *precondition* (a `require` did not hold), *unverified* (nothing proved an
expected result).
_Avoid_: blaming the test for a step that could not run (the app may be the one at fault).

**Expected result** (`E1`, `E2`…): one outcome a case must prove, numbered by order in its
case and proven by a `check --for`.
_Avoid_: assertion, acceptance criterion.
