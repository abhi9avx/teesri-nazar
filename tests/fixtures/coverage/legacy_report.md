# Teesri Nazar report: legacy

| | |
|---|---|
| App | com.example.app 1.0 |
| Device | Pixel (fake-1), Android 15 |
| Started | 2026-10-21T10:00:00.000Z |
| Duration | 5m 00s |

**Request:** test login

## Summary

4 case(s): ✅ 1 pass  ❌ 1 fail  ⛔ 1 blocked  ⏭ 1 skipped  | checks 2/3 passed

| Case | Title | Result | Checks | Reason |
|---|---|---|---|---|
| TC-01 | Login works | ✅ pass | 1/1 |  |
| TC-02 | Wrong MPIN | ❌ fail | 0/1 | failed: attempts incremented |
| TC-03 | Offline | ⛔ blocked | 0/0 | network down |
| TC-04 | Out of scope | ⏭ skipped | 0/0 | not in this release |
| - | outside any case | ✅ pass | 1/1 |  |

## ✅ TC-01: Login works

*PASS* — 1 step(s), 30s

- ✅ [ui] Home visible ([screenshot](shots/a.png))

## ❌ TC-02: Wrong MPIN

*FAIL* — 0 step(s), 30s — failed: attempts incremented

- ❌ [db] attempts incremented — got 0
- 📝 **bug**: no error shown
- evidence: [shots/b.png](shots/b.png)
- evidence: [logs/l.txt](logs/l.txt)

## ⛔ TC-03: Offline

*BLOCKED* — 0 step(s), 10s — network down


## ⏭ TC-04: Out of scope

*SKIPPED* — 0 step(s), 10s — not in this release


## ✅ -: outside any case

*PASS* — 0 step(s)

- ✅ [ui] loose check

## Bugs found

- no error shown (case TC-02)
