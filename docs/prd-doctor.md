# PRD — `tnazar doctor`: tell a new user what is wrong with their setup and how to fix it

| | |
|---|---|
| Status | **Built v0.2** on `feature/doctor` (2026-10-20): design grilled and agreed (Q1–Q9, E1–E2 approved); refinements E3–E5 found on a real cold boot and a physical phone (§9). |
| Branch | `feature/doctor` (from `main`) |
| Date | 2026-10-20 |
| Test plan | [test-plan-doctor.md](test-plan-doctor.md) |
| Roadmap | `product/bets.md` → Now #2 |
| Related | ARCHITECTURE.md §6 (reading the screen, UiAutomator2), §11 (exit codes); README *Troubleshooting*; `GLOSSARY.md` (*ready device*, *blocker*, *warning*) |

---

## 1. Context gate

- **Strategy context / ICP** [RESOLVED FROM WORKSPACE: product/strategy.md (Last updated:
  2026-10-19)]: QA engineers and SDETs at 10–200 person Android teams who already use Claude
  Code. Focus: **Engagement**, meaning people outside the project finish a real run on their own app.
- **Evidence base: [STRATEGY-DRIVEN ONLY - NO COMPELLING EVIDENCE].** No outside user has
  installed it yet. The bet rests on two observations from the founder's own use:
  - [ASSUMPTION] A first-time user's first failure is setup (adb, an unauthorised phone, an old
    Python), not the feature under test.
  - [FACT, code] Today an unauthorised or offline phone is reported as *"no device connected
    (adb devices is empty)"* (`teesri_nazar/adb.py` `pick_serial`), which is false and sends the
    user the wrong way.
- **Stage and constraints:** pre-launch open source, one part-time builder. Appetite: **2 days**.
  Rules kept: standard library only, Python 3.9+, read-only towards the device.

## 2. School

**Shape Up pitch.** The appetite is fixed (2 days) and the scope flexes to fit. There is no outcome
to discover here: the problem is concrete and the solution space is small.

## 3. Problem

A new user clones the repo and runs `./tnazar start …` or asks Claude to test a feature. When the
setup is wrong they get one of:

| Their setup | What they see today | Why it hurts |
|---|---|---|
| No adb | `adb not found: install platform-tools or set ANDROID_HOME` | Good, but only after they tried a real command. |
| Phone plugged in, USB debugging prompt not accepted | `no device connected (adb devices is empty)` | **False**: adb lists it as `unauthorized`. |
| Phone `offline` / Linux `no permissions` | same false message | Same. |
| Python 3.8 or older | a traceback on import, or a `SyntaxError` | No fix is shown. |
| UiAutomator2 server missing | `screen reads: uiautomator dump (~2 s per read)` at `start` | Good, but only inside a run. |
| Two phones | `several devices connected (…); pass --device <serial>` | Good. |

Each one costs a first-time user minutes and costs the agent turns. Every setup problem should show
up in **one command, before any run**, with the fix printed next to it.

## 4. Goals and non-goals

**Goals**

1. `./tnazar doctor` runs every setup check in one go (target < 3 s on a ready device) and
   prints one line per check, plus the fix for each warning or blocker.
2. The exit code says whether runs can start: `0` = yes (maybe with warnings), `2` = a blocker.
3. Every command run on old Python prints the fix, not a traceback.
4. Every command that finds no ready device says why, and points to `doctor`.

**Non-goals (no-gos for this batch)**

- Fixing anything automatically: no `adb install`, no `adb kill-server`, no server start (Q2).
- Starting the UiAutomator2 server to prove it answers (up to 15 s; breaks an active run) (Q2).
- `--json` output (Q7).
- Checks beyond the six in §5: no Android version, storage, animations, network, Appium (Q1).
- Telemetry of any kind.

## 5. Solution

### 5.1 Checks, in order

| # | Check | OK when | Severity on failure | Fix printed |
|---|---|---|---|---|
| C1 | **Python** | `sys.version_info >= (3, 9)` (shows version and `sys.executable`) | blocker | Install Python 3.9+; run `python3.9 ./tnazar …` or put it first on PATH |
| C2 | **adb** | found via `ANDROID_HOME` / `ANDROID_SDK_ROOT` / PATH, and `adb version` runs | blocker | Install Android platform-tools, or set `ANDROID_HOME`. It names a set variable with no adb inside, in the fix (not found) or the detail (found on PATH instead) |
| C3 | **devices** | at least one device listed by `adb devices -l` | blocker | Plug in a phone with USB debugging on, or start an emulator |
| C4 | **device state** (per listed device) | state `device` **and** `sys.boot_completed` = 1 = ready device | blocker if **no** device is ready; a listed device that is not ready gets its own line and fix | `unauthorized`: accept the *Allow USB debugging* prompt on the phone (re-plug if it is gone). `offline`: re-plug, or `adb kill-server && adb start-server`. `no permissions`: add a udev rule (Linux). `recovery`/`bootloader`/`sideload`: boot the phone normally. *still booting*: wait for the home screen. Listed but not answering: re-plug or restart adb. |
| C5 | **screen size** (per ready device) | `wm size` parses | blocker for that device: percentage taps, `scroll` and `swipe` need it | Unlock the phone and run `adb -s <serial> shell wm size`; report a bug with the output |
| C6 | **fast screen reads** (per ready device) | `io.appium.uiautomator2.server.test` instrumentation is installed | **warning**: runs work at ~2 s per read | The same text `start` prints today (`uia2.start_line`'s install branch): the `adb install` commands for local APKs, or how to get them via Appium |

Several ready devices: all are checked, and the output ends with `pass --device <serial> or set
ANDROID_SERIAL` (a warning). `--device <serial>` / `ANDROID_SERIAL` narrows the checks to one
device. A serial that is not listed is a blocker.

C2 failing skips the device checks: one `SKIP devices` line gives the reason. C3 failing ends
the list there, because there is no device to check.

### 5.2 Output (breadboard)

```
$ ./tnazar doctor
OK    python: 3.9.6 (/usr/bin/python3)
OK    adb: /Users/me/Library/Android/sdk/platform-tools/adb (35.0.2)
FAIL  device R58M123: unauthorized
        fix: accept the "Allow USB debugging" prompt on the phone; re-plug it if the prompt is gone
OK    device emulator-5554: ready (Pixel_9_Pro)
OK    emulator-5554 screen: 1344x2992
WARN  emulator-5554 screen reads: slow, uiautomator dump (~2 s per read)
        fix: install the UiAutomator2 server once:
          adb -s emulator-5554 install -r -g …/appium-uiautomator2-server-v7.0.0.apk
          adb -s emulator-5554 install -r -g …/appium-uiautomator2-server-debug-androidTest.apk
1 blocker, 1 warning: runs can start on emulator-5554 only
```

Rules: the status column comes first and has a fixed width. Fix lines are indented under the line
they fix. The last line is a one-line verdict. No colour codes (the agent reads this output). No
secrets and no app content: serials, models and paths only.

The same device can be both blocked and ready. In the example, R58M123 is blocked and the run can
still use emulator-5554, so the exit code is `0`. **Exit 2 only when no device can run.** The
verdict line names the usable devices.

### 5.3 Exit codes (ARCHITECTURE.md §11, unchanged meanings)

| Code | When |
|---|---|
| `0` | At least one ready device passes C4–C5 and C1–C2 pass (warnings allowed) |
| `2` | Any blocker in C1–C3, or no device passes C4–C5, or `--device` names an unlisted serial |

### 5.4 Old Python in the launcher (Q4)

`./tnazar` checks `sys.version_info` **before importing the package** and, below 3.9, prints
`tnazar needs Python 3.9+; this is <version> at <path>. …fix…` to stderr and exits `2`. The
launcher must therefore parse on Python 3.0+ (no f-strings or annotations). The pip entry point is
already protected by `requires-python = ">=3.9"`.

### 5.5 `pick_serial` says why (Q6)

When no device is ready, but some are listed, every command's error becomes:

```
no ready device: R58M123 is unauthorized (accept the "Allow USB debugging" prompt on the phone).
Run ./tnazar doctor for a full check.
```

The empty case stays `no device connected (adb devices is empty)`, followed by the same
`doctor` hint. The fix text has a single source shared with C4, so the two can't drift.

### 5.6 Module shape (technical design)

- New `teesri_nazar/doctor.py`, which holds all the logic and is pure where it can be:
  - `Check` dataclass: `status` (`ok|warn|fail|skip`), `name`, `detail`, `fix: list[str]`.
  - `state_fix(state) -> str | None`: the fix text for a device state (shared with `pick_serial`).
  - `run_checks(python=sys.version_info, find_adb=adb_binary, devices=list_devices,
    adb_for=Adb, only=None) -> list[Check]`: dependencies are injected, so L1 tests need no
    patching.
  - `verdict(checks) -> tuple[int, str]`: the exit code and the last line.
- `cli.cmd_doctor`: `--device` → `only`; prints each check and returns the code from `verdict`.
  It doesn't touch the active run, never calls `Ctx.from_args`, and never claims the device.
- `uia2.py`: split the install-hint branch out of `start_line` into `install_hint(adb) -> list[str]`
  so `start` and `doctor` print the same commands. Behaviour of `start_line` unchanged.
- `adb.py`: `list_devices` ignores `* daemon …` lines and parses multi-word states such as
  `no permissions (…)`. The state-to-fix map `STATE_FIX` lives in `adb.py`, and `pick_serial`
  and `doctor.state_fix` both read it, so imports run one way only (`doctor` → `adb`).
- Timeouts: `adb version` 5 s, `adb devices` 10 s (the first call starts the adb server), and
  10 s for every question to a device (boot state, `wm size`, instrumentation). A timeout fails that check and
  never hangs.

### 5.7 Agent and docs (Q8)

- `CLAUDE.md` (tester playbook) *Rules*: "If `start` exits 2 with a device or setup error, run
  `./tnazar doctor` and follow its fix lines."
- `.claude/skills/test-feature/SKILL.md`: the same line in its setup step.
- README: *Quick start* gets `./tnazar doctor` as the first command. *Troubleshooting* gets a
  row: "anything about adb, devices or Python → `./tnazar doctor`".
- ARCHITECTURE.md: a `doctor` row in the command table, and §11 notes that `doctor` uses 0/2.
- CHANGELOG: an entry with the v4 release. `site/build.py` accepts only `## vN: Title`
  headings and drops text above the first one, so there is no *Unreleased* section to hold
  it before then. Paste this into the v4 section when it ships:

  ```markdown
  - **`./tnazar doctor`**: one command checks Python, adb, every connected device (unauthorised,
    offline, still booting, not answering) and fast screen reads, and prints the fix under each
    problem. When a phone is not listed at all, it names the usual causes (USB debugging off, a
    charge-only cable). Run it first on a new machine.
  - When devices are connected but none is ready, every command now names each one and why
    (for example `unauthorized`), instead of wrongly saying "adb devices is empty", and points to
    `doctor`.
  ```

## 6. Risks and rabbit holes

| Risk | Handling |
|---|---|
| adb output differs across versions (daemon banner, multi-word states) | Parse fixtures from real adb outputs in the test plan (adb 33–35, Linux `no permissions`). |
| `adb devices` hangs while the adb server starts | 10 s timeout; a timeout is a blocker with fix `adb kill-server && adb start-server`. |
| Python check is untestable on CI's 3.9 | Launcher logic is tested by running the launcher with a patched `sys.version_info` (test plan PY-*). |
| Scope creep into "fix it for me" | No-go (§4). Revisit only with interview evidence. |
| Import cycle `adb` ↔ `doctor` | The fix-text map lives in `adb.py`; `doctor` imports it. |

## 7. Done when (from bets.md, made testable)

1. A fresh clone with **no device** → `doctor` exits 2, says no device is connected and prints the fix.
2. An **unauthorised** device → exits 2, names the serial, prints the prompt fix; `start` says
   the same and points to `doctor`.
3. A **ready** device → exits 0. Without the server it shows WARN with the install commands;
   with the server everything is OK.
4. Python < 3.9 → the launcher prints the fix and exits 2 (simulated in tests).
5. Test plan exit criteria (§9 there) met: suite green on `/usr/bin/python3` (3.9.6), one
   `ai-code-review`, and the device smoke seen working on the emulator.

## 8. Success measure

[ASSUMPTION, revisit after interview 5] In the five v4 interviews, nobody who runs the setup
needs the founder's help past `./tnazar doctor`. Kill / rethink: if 2 or more of 5 get stuck
on setup that `doctor` reported OK, its checks are the wrong ones. Note what they hit and
re-shape. No telemetry: this is observed in the interviews.

## 9. Decisions log (grilling, 2026-10-20, all accepted)

Q1 six checks only · Q2 read-only, server installed ≠ started · Q3 WARN for slow reads, exit 0/2
· Q4 Python check in the launcher · Q5 all devices, `--device` narrows · Q6 fix `pick_serial` in
the same change · Q7 text only, no `--json` · Q8 CLAUDE.md, skill, README, CHANGELOG · Q9 glossary
yes, no ADR.

**Engineering calls made while writing this (please confirm in review):**

- E1: screen size unreadable is a **blocker for that device**, because percentage taps, `scroll`
  and `swipe` depend on it.
- E2: exit 2 only when **no** device can run. An unauthorised second phone next to a ready
  emulator is reported but doesn't block.

E1 and E2 were approved on 2026-10-20.

**Found while building (2026-10-20):**

- E3: on a cold boot adb lists the emulator as `device` about 4 s before Android has started.
  `doctor` then said "slow reads" (`pm` was not up yet) rather than "still booting". C4 now
  also reads `sys.boot_completed`, at one extra adb call per device. Seen live on a cold boot:
  *no device* → *offline* → *still booting* → *ready*.
- E4: a device call that timed out escaped `doctor` as an unhandled error. It is now that
  device's blocker (*listed but not answering*).
- E5 (physical phone, 2026-10-21): with USB debugging off, `--device <serial>` said only "check
  the serial", but the real cause was the phone's settings or cable. The fix for *not listed*
  and *no device connected* now lists the usual causes (USB debugging off, a charge-only cable,
  a USB mode that blocks data: *File transfer / MTP*), then the listed serials in case of a typo.
  The dashboard smoke on that phone fails DA-04 and DA-12 on `origin/main` too: the
  smoke script assumes AOSP Settings screens, so it is not app-agnostic. That is separate work.
