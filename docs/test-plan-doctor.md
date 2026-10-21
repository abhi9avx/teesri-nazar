# Test plan — `tnazar doctor`

| | |
|---|---|
| Status | **v0.2**: implemented in `tests/test_doctor.py` (61 tests); device smoke done on the Pixel 9 Pro emulator |
| PRD | [prd-doctor.md](prd-doctor.md) (section refs below are PRD sections) |
| Branch | `feature/doctor` |
| Rule | **Test first** (`tdd`): one failing test, then the code that makes it pass, one behaviour at a time. Phase 0 tests pass on today's code before any change and stay green. |

---

## 1. Strategy

| Level | Proves | Where | When |
|---|---|---|---|
| **L1 Pure** | `run_checks` with injected fakes, `verdict`, `state_fix`, `list_devices` parsing, `install_hint` | `tests/test_doctor.py` | every commit, CI (Python 3.9) |
| **L2 CLI in-process** | `tnazar doctor` and `pick_serial` errors through `cli.main`: stdout, exit code, the adb calls made | `tests/test_doctor.py`, `tests/test_cli.py` | every commit, CI |
| **L2 Launcher** | `./tnazar` under a patched `sys.version_info` | `tests/test_doctor.py` | every commit, CI |
| **L4 Device smoke** | ready emulator, emulator with the server, no device | manual, steps in §6 | before any push |

**Seams:** `doctor.run_checks(...)` with injected callables (no `mock.patch` needed). The CLI goes
through `tnazar()` from `fakedevice.py`, and the adb boundary through `FakeAdb.calls`. Tests never
assert on exact wording beyond the stable tokens (`OK`, `WARN`, `FAIL`, `SKIP`, serial, state,
the fix's key command), so the copy can change.

**Python 3.9:** no `X | None` outside annotations, no `match`. Run `/usr/bin/python3 -m unittest
discover -s tests`.

## 2. Fixtures

`tests/fixtures/adb/` holds real `adb devices -l` outputs as text files (no app content, fake serials):

| File | Content |
|---|---|
| `empty.txt` | header only |
| `daemon_start.txt` | `* daemon not running; starting now at tcp:5037` + `* daemon started successfully` + header + one ready device |
| `ready.txt` | one `device` with `product: model: device: transport_id:` |
| `unauthorized.txt` | one `unauthorized` (no model field) |
| `offline.txt` | one `offline` |
| `no_permissions.txt` | Linux: `no permissions (missing udev rules? …); see [http://…]` |
| `mixed.txt` | one `unauthorized` + one ready emulator |
| `two_ready.txt` | two ready devices |
| `recovery.txt` | one `recovery` |

`FakeAdb` change (test code only): `booting` (adb lists the device but `sys.boot_completed` is
not 1). An unreadable size and a hung device are simulated by replacing that `FakeAdb`'s
`screen_size` / `shell` with a `Mock` raising `AdbError`. L1 tests inject `FakeAdb`s through
`adb_for`, so no patching is needed.

## 3. Phase 0 — characterisation (green on today's code)

| ID | Test |
|---|---|
| P0-01 | `pick_serial()` with one ready device returns it; with `ANDROID_SERIAL` set returns that; with two ready raises `several devices`. Pins current behaviour before the change. |
| P0-02 | `uia2.start_line` on a device without the server and with local APKs prints the same `adb -s … install -r -g …` lines as today (pins the text before `install_hint` is extracted). |
| P0-03 | `list_devices()` on `ready.txt` → one device, state `device`, model parsed. |

## 4. L1 / L2 cases

### PARSE — `list_devices` (PRD §5.6)

| ID | Given | Expect |
|---|---|---|
| PARSE-01 | `daemon_start.txt` | exactly one device; the `*` lines are ignored |
| PARSE-02 | `no_permissions.txt` | state `no permissions`, not `no` |
| PARSE-03 | `unauthorized.txt` | state `unauthorized`, model `""` |
| PARSE-04 | `empty.txt` | `[]` |
| PARSE-05 | trailing blank lines, CRLF line ends (Windows adb via WSL) | parsed the same |
| PARSE-06 | every state adb prints (offline, recovery, sideload, bootloader, authorizing, connecting) | parsed as that state |
| PARSE-07 | `adb devices` hangs | `AdbError` after 10 s, no hang |
| PARSE-08 | `adb server version (41) doesn't match this client` banner | not a device |

### PY — Python check (C1, §5.4)

| ID | Given | Expect |
|---|---|---|
| PY-01 | `run_checks(python=(3, 9, 6))` | C1 `OK`, shows `3.9.6` and the interpreter path |
| PY-02 | `run_checks(python=(3, 8, 10))` | C1 `FAIL`, fix mentions 3.9+ |
| PY-03 | launcher run via `runpy` with `sys.version_info` patched to `(3, 8, 0)` | stderr has `needs Python 3.9+`, `SystemExit(2)`, **`teesri_nazar` not imported** (`sys.modules` check) |
| PY-04 | launcher source compiles under the 3.0 grammar subset we promise: no f-strings, no annotations (an `ast` scan for `JoinedStr` / `AnnAssign`) | passes |

### ADB — adb found (C2)

| ID | Given | Expect |
|---|---|---|
| ADB-01 | `find_adb` raises `AdbError` | C2 `FAIL`, fix names platform-tools / `ANDROID_HOME`; one `SKIP devices` line; devices never listed; exit 2 |
| ADB-02 | `ANDROID_HOME` set to a dir without adb, adb on PATH | C2 `OK` with the PATH adb; detail notes `ANDROID_HOME` has no adb |
| ADB-03 | adb found but `adb version` raises `OSError` (not executable) | C2 `FAIL` with the path and a fix |
| ADB-04 | `adb version` times out | C2 `FAIL`, fix `adb kill-server && adb start-server`; returns within the timeout |
| ADB-05 | adb not found, `ANDROID_SDK_ROOT` set to a folder without adb | the fix names `ANDROID_SDK_ROOT=… has no adb` |

### DEV — devices and state (C3–C4)

| ID | Given | Expect |
|---|---|---|
| DEV-01 | `empty.txt` (**done-when #1**) | C3 `FAIL` "no device connected", fix: plug in / start emulator, and the plugged-in-but-not-listed checklist; exit 2 |
| DEV-02 | `unauthorized.txt` (**done-when #2**) | `FAIL device <serial>: unauthorized`, fix mentions the *Allow USB debugging* prompt; exit 2 |
| DEV-03 | `offline.txt` | `FAIL`, fix re-plug / restart adb server; exit 2 |
| DEV-04 | `no_permissions.txt` | `FAIL`, fix mentions udev; exit 2 |
| DEV-05 | `recovery.txt` | `FAIL`, fix boot normally; exit 2 |
| DEV-06 | `mixed.txt` (PRD E2) | the unauthorized line `FAIL`, emulator `OK`; **exit 0**; verdict names the emulator as usable |
| DEV-07 | `two_ready.txt` | both checked; a `WARN` "pass --device or set ANDROID_SERIAL"; exit 0 |
| DEV-08 | `two_ready.txt` + `--device <second>` | only the second device's C4–C6 lines; no WARN about several |
| DEV-09 | `--device NOPE` | `FAIL` "NOPE is not listed by adb"; fix names USB debugging, File transfer, a data cable, then the listed serials; exit 2 |
| DEV-10 | `ANDROID_SERIAL=<first>` with `two_ready.txt` | same as `--device <first>` |
| DEV-11 | `adb devices` times out | C3 `FAIL` with the restart-server fix; no hang |
| DEV-12 | listed `device` but `sys.boot_completed` ≠ 1 (PRD E3) | `FAIL … still booting`, no screen-reads line, exit 2 |
| DEV-13 | the device's adb call times out (PRD E4) | `FAIL … not answering`, restart-adb fix, exit 2 |
| DEV-14 | every question to a device | asked with a timeout ≤ `DEVICE_TIMEOUT` (10 s), including `wm size` |
| DEV-15 | adb disappears mid-check (`OSError`) | that device's blocker, not a traceback |
| DEV-16 | check labels renamed | `usable()` unchanged: readiness comes from `Check.kind`, not the label |

### SIZE — screen size (C5, PRD E1)

| ID | Given | Expect |
|---|---|---|
| SIZE-01 | ready `FakeAdb` 1080×2400 | `OK <serial> screen: 1080x2400` |
| SIZE-02 | `wm size` prints `Physical size: 1080x2400` + `Override size: 720x1600` | shows `720x1600` (matches `Adb.screen_size`) |
| SIZE-03 | `wm size` unparseable | `FAIL` for that device; it is not usable; exit 2 if it is the only device |

### UIA2 — fast reads (C6)

| ID | Given | Expect |
|---|---|---|
| UIA2-01 | `uia2_installed = True` | `OK <serial> screen reads: fast` |
| UIA2-02 | not installed, APKs under `APPIUM_HOME` | `WARN`, fix lines equal `uia2.install_hint(adb)`; **exit 0** (done-when #3) |
| UIA2-03 | not installed, no APKs | `WARN`, fix mentions `appium driver install uiautomator2` |
| UIA2-04 | `start_line` after the `install_hint` extraction | byte-for-byte the P0-02 output |

### SAFE — read-only (Q2)

| ID | Given | Expect |
|---|---|---|
| SAFE-01 | full `doctor` on a ready `FakeAdb` | `FakeAdb.calls` contains only `shell getprop sys.boot_completed`, `shell wm size`, `shell pm list instrumentation` (`version` and `devices` go through the adb binary, not the device). **No** `install`, `forward`, `am instrument`, `settings put`, `kill-server` |
| SAFE-02 | `doctor` while a run is active (`start` first) | the active run, `uia2.json` and the device owner marker are unchanged; the run continues (`observe` works after) |
| SAFE-03 | `doctor` with no `TNAZAR_HOME/runs` | creates no files under `TNAZAR_HOME` |

### PICK — `pick_serial` says why (Q6, §5.5)

| ID | Given | Expect |
|---|---|---|
| PICK-01 | `unauthorized.txt`, any command (`tnazar devices` excluded) e.g. `start demo` | exit 2; stderr has the serial, `unauthorized`, the prompt fix and `./tnazar doctor` |
| PICK-02 | `empty.txt` | `no device connected (adb devices is empty)` + the doctor hint |
| PICK-03 | `offline` + `unauthorized` | both serials named, each with its fix |
| PICK-04 | the fix text in PICK-01 equals `doctor`'s fix for the same state (single source) | equal |
| PICK-05 | `tnazar start demo` with only an unauthorised phone | exit 2; stderr names it and `./tnazar doctor` |
| PICK-06 | an unknown state | named, with the generic re-plug / restart-adb fix |

### OUT — output and exit codes (§5.2–5.3)

| ID | Given | Expect |
|---|---|---|
| OUT-01 | every scenario above | each line starts with one of `OK␣␣␣␣`, `WARN␣␣`, `FAIL␣␣`, `SKIP␣␣`; fix lines are indented under their check |
| OUT-02 | all OK | the last line says runs can start; exit 0 |
| OUT-03 | any blocker | the last line counts blockers and warnings; exit 2 |
| OUT-04 | no ANSI escape codes in any output | true |
| OUT-05 | `verdict()` table test over (blockers, warnings, usable devices) | 0/2 as in §5.3; no checks at all → 2 |
| OUT-06 | `tnazar doctor --help` | lists `--device` |

### PERF — budget

| ID | Given | Expect |
|---|---|---|
| PERF-01 | ready `FakeAdb` | at most 3 adb calls per device + 2 global (pins the cost, like `DeviceBudgetTest`); measured 0.28 s on the emulator |

## 5. Docs checks

| ID | Expect |
|---|---|
| DOC-01 | `CLAUDE.md` and `.claude/skills/test-feature/SKILL.md` mention `./tnazar doctor` in the setup/rules step |
| DOC-02 | README *Quick start* and *Troubleshooting* mention `./tnazar doctor`; `tests/test_site_build.py` still passes (site built from README/CHANGELOG) |
| DOC-03 | ARCHITECTURE.md command table has `doctor`; §11 is unchanged in meaning |

## 6. L4 — device smoke (manual, before any push)

On the Pixel 9 Pro emulator, recording what was seen, not what should happen:

1. Emulator running with the server installed → `./tnazar doctor` → all OK, exit 0.
2. `adb uninstall io.appium.uiautomator2.server.test` → `WARN` with install commands, exit 0.
   Run the printed commands → back to OK.
3. `adb emu kill` (no device) → `FAIL` no device, exit 2. `./tnazar start x` names doctor.
4. Real phone with USB debugging revoked (*Revoke USB debugging authorisations*), if one is
   available → `unauthorized` FAIL with the prompt fix. Otherwise mark it as **not run** and
   rely on DEV-02 and PICK-01.
5. During an active run (`./tnazar start smoke` …) → `doctor` exits 0 and `observe` still works.
6. Time `doctor` on the ready emulator: target < 3 s. Over 5 s → root-cause it (slow-steps rule).

## 7. Exit criteria (before any push or PR)

- All tests in §3–§5 written first, seen failing, then passing.
- Full suite green on `/usr/bin/python3` (3.9.6).
- One `ai-code-review` against `main`; HIGH/MED fixed, LOW listed.
- §6 smoke done and reported on the exact commit to be pushed.
- The content scrub is clean: fake serials only, no app or employer names, nothing app-specific.
