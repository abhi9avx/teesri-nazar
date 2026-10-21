# Teesri Nazar — Architecture (HLD)

This document explains **what Teesri Nazar is, why it exists, and how every part of it
works**, from "you type a request" down to "a finger tap lands on the phone". It is
written so that a student who has never touched Android automation can follow it, and
an engineer can use it as the design reference.

> **Teesri Nazar** (तीसरी नज़र) means *"the third eye"*. Dev builds it, PM designs it,
> Teesri Nazar checks it.

---

## Contents

1. [The problem](#1-the-problem)
2. [The idea in one sentence](#2-the-idea-in-one-sentence)
3. [Words you need first](#3-words-you-need-first)
4. [The big picture](#4-the-big-picture)
5. [Layer 1 — Talking to the phone (ADB)](#5-layer-1--talking-to-the-phone-adb)
6. [Layer 2 — Reading the screen (the UI dump)](#6-layer-2--reading-the-screen-the-ui-dump)
7. [Layer 3 — Finding an element (selectors, not XPath)](#7-layer-3--finding-an-element-selectors-not-xpath)
8. [Layer 4 — Acting on the phone](#8-layer-4--acting-on-the-phone)
9. [Layer 5 — Verifying (checks)](#9-layer-5--verifying-checks)
10. [Layer 6 — Recording a run](#10-layer-6--recording-a-run)
11. [Layer 7 — Pass / fail and the report](#11-layer-7--pass--fail-and-the-report)
12. [The brain — how Claude decides](#12-the-brain--how-claude-decides)
13. [One request, end to end](#13-one-request-end-to-end)
14. [Replay — running a recording again](#14-replay--running-a-recording-again)
15. [The live dashboard](#15-the-live-dashboard) (15.1 running a whole plan, 15.2 the app map, 15.3 the learned map)
16. [Design decisions and trade-offs](#16-design-decisions-and-trade-offs)
17. [How the tool itself is tested](#17-how-the-tool-itself-is-tested)
18. [Limits](#18-limits)
19. [Code map](#19-code-map)

---

## 1. The problem

Every new feature in a mobile app needs someone to **open the app on a phone and check
that it works**: tap through the flow, type the inputs, look at the result, try the edge
cases, take screenshots, and write up what passed and what failed.

Today that is done in one of two ways, and both hurt:

| Approach | What goes wrong |
|---|---|
| **Manual testing** | Slow, repetitive, easy to skip edge cases, evidence (screenshots, logs) is collected by hand or not at all, and nothing is left behind to re-run next release. |
| **Classic automation** (Appium, Espresso, scripts) | Someone has to *write and maintain code* for every screen. Selectors break when the UI changes. A new feature can't be tested until the automation is written — which is usually after the feature ships. |

What we actually want is a **tester**, not a script: something you can hand a feature
description and say *"test this"*, which decides what to check, does it on a real device,
and gives you a report with proof.

## 2. The idea in one sentence

> An AI agent (**Claude Code**) plays the role of a senior manual tester, and a small
> Python command-line tool (**`tnazar`**) gives it eyes and hands on an Android phone —
> while recording every step so the session becomes a report *and* a replayable test.

The split is deliberate:

- **Claude** does what humans are good at: reading a PRD, deciding what to test, noticing
  something looks wrong, choosing the next step.
- **`tnazar`** does what machines are good at: talking to the device precisely, finding
  elements reliably, waiting correctly, recording everything, and computing pass/fail
  from the evidence — the same way every time.

---

## 3. Words you need first

| Term | Plain meaning |
|---|---|
| **ADB** (Android Debug Bridge) | A command-line program from Google that lets a computer control an Android phone over USB or Wi-Fi: run shell commands on it, simulate taps, take screenshots, read logs. |
| **Emulator** | A virtual Android phone running on your computer. ADB treats it like a real one. |
| **UIAutomator** | A tool built into Android. Its `uiautomator dump` command writes the *current screen* as an XML file. |
| **UI tree / accessibility tree** | The structure Android keeps of what is on screen: every button, text, input box, nested inside layouts. Screen readers for blind users use the same tree. |
| **Node / element** | One item in that tree, e.g. a button. |
| **`resource-id`** | A developer-given name for an element, like `com.app:id/btn_login`. Stable across releases — the best way to find something. |
| **`content-desc`** | The accessibility label (what a screen reader would say), e.g. `"Navigate up"`. |
| **`text`** | The visible text, e.g. `"Pay now"`. |
| **`bounds`** | The element's rectangle on screen in pixels: `[left,top][right,bottom]`. |
| **Selector** | A rule for finding an element: "the one whose id is `btn_login`", or "the one whose text is `Pay now`". |
| **XPath** | A query language for XML used by tools like Appium (`//android.widget.Button[@text='Login']`). **Teesri Nazar does not use XPath** — see [§7](#7-layer-3--finding-an-element-selectors-not-xpath). |
| **Run** | One testing session, saved to `runs/<name>/`. |
| **Case** | One test case inside a run, e.g. `TC-01 Login with valid MPIN`. |
| **Check** | One verified expectation inside a case. Pass or fail. |
| **MCP** | Model Context Protocol — how Claude connects to other tools (Figma, Jira/Confluence). |

---

## 4. The big picture

```mermaid
flowchart TB
    subgraph You["🧑 You"]
        IN["Feature request<br/>text · test cases · PRD · Figma · Jira"]
    end

    subgraph Brain["🤖 Claude Code — the tester's brain"]
        PB["CLAUDE.md playbook<br/>+ test-feature skill"]
        DEC["Plan → decide next step<br/>→ interpret what it sees"]
    end

    subgraph Hands["🧰 tnazar — eyes and hands (Python, stdlib only)"]
        CLI["cli.py<br/>commands"]
        SCR["screen.py<br/>read UI tree · find elements"]
        DEV["device.py<br/>tap · type · scroll · screenshot · logs"]
        ADB["adb.py<br/>run adb"]
        SES["session.py<br/>run folder · event log"]
        REP["report.py<br/>pass/fail · MD + HTML"]
        RPL["replay.py<br/>re-run a recording"]
    end

    PHONE["📱 Android phone / emulator"]
    EXT["🗄️ DB (read-only) · 🎨 Figma · 📋 Jira/Confluence"]
    OUT["📄 runs/NAME/report.html<br/>screenshots · logs · events"]

    IN --> PB --> DEC
    DEC -->|"shell: ./tnazar tap 'Pay now'"| CLI
    CLI -->|"text output: what happened"| DEC
    CLI --> SCR & DEV & SES
    SCR --> ADB
    DEV --> ADB
    ADB <-->|USB / TCP| PHONE
    DEC -.->|MCP / queries| EXT
    SES --> REP --> OUT
    RPL --> CLI
```

Read it top to bottom:

1. **You** describe the feature.
2. **Claude** follows the playbook in `CLAUDE.md`, writes a test plan, and then drives the
   device one command at a time.
3. Each command is a normal shell call to **`./tnazar`**. It does one precise thing,
   prints the result as text, and appends an event to the run's log.
4. `tnazar` talks to the **phone** only through **ADB**.
5. At the end, the event log becomes a **report**.

Claude never touches the phone directly, and `tnazar` never decides what to test. That
boundary is the most important design choice in the project.

---

## 5. Layer 1 — Talking to the phone (ADB)

*File: `teesri_nazar/adb.py`*

Everything that reaches the device goes through one small class, `Adb`. It runs the
`adb` program as a subprocess:

```
adb -s <serial> shell input tap 540 860
└┬┘ └────┬────┘ └──────────┬──────────┘
 │       │                 └ the command to run on the phone
 │       └ which device (there may be several)
 └ the adb binary (found via $ANDROID_HOME/platform-tools, or $PATH)
```

**Which device?** `pick_serial()` decides, in order:

1. `--device <serial>` if you passed one,
2. the device of the active run,
3. `$ANDROID_SERIAL`,
4. the only connected device — or an error asking you to choose if there are several.

When no device is ready, the error names each listed device and why it is not ready
(`R58M… is unauthorized (accept the "Allow USB debugging" prompt …)`) and points to
`./tnazar doctor`. The state-to-fix text (`STATE_FIX`) is shared with `doctor`.

**Checking the setup: `tnazar doctor`** (*file: `teesri_nazar/doctor.py`*). One command checks
Python 3.9+, adb (and its version), every listed device's state (including *still booting*:
adb says `device` before Android has started), the screen size and whether the UiAutomator2
server is installed. Each problem gets a `fix:` line. It only reads: it never installs, starts
the server or claims the device, so it is safe while a run is in progress and is not held by
a dashboard pause. Exit 0 = a run can start on at least one device (warnings allowed, such as
slow reads); 2 = nothing can run until a blocker is fixed. The `./tnazar` launcher checks the
Python version before importing anything, so old Python prints the fix instead of a traceback.

**Everything is synchronous with a timeout.** If adb hangs (it can), the call fails with
a clear `AdbError` instead of freezing the run.

The ADB commands `tnazar` relies on:

| Need | ADB command used |
|---|---|
| Read the screen | `uiautomator dump` (see §6) |
| Tap / long-press | `input tap x y` / `input swipe x y x y 800` |
| Type | `input text "..."` |
| Press a key | `input keyevent 4` (back), `66` (enter), … |
| Swipe / scroll | `input swipe x1 y1 x2 y2 ms` |
| Screenshot | `exec-out screencap -p` (PNG bytes straight back) |
| Launch app | `cmd package resolve-activity` → `am start -W -n <activity>` (fallback: `monkey`) |
| Clear app data | `am force-stop` + `pm clear` |
| Deep link | `am start -a android.intent.action.VIEW -d <url>` |
| App version | `dumpsys package <pkg>` → `versionName`, `versionCode` |
| Crash / ANR | `logcat -b crash` and `logcat -b system -e "ANR in"` |
| Speed up UI | `settings put global *_animation_scale 0` |

---

## 6. Layer 2 — Reading the screen (the UI dump)

*File: `teesri_nazar/screen.py` → `capture()`, `parse()`*

A human tester *looks* at the screen. `tnazar` *reads* it. Android can describe its
current screen as XML, and that XML is the single source of truth for "what is on screen".

### 6.1 Getting the dump — in one round trip

```bash
adb exec-out "rm -f /sdcard/tnazar-ui.xml; \
              uiautomator dump /sdcard/tnazar-ui.xml >/dev/null 2>&1; \
              cat /sdcard/tnazar-ui.xml"
```

Dump to a file on the phone and print it back, all in **one** adb call (~2–3 s). If the
screen is mid-animation, `uiautomator` sometimes returns nothing; `capture()` retries up
to 3 times with a growing pause.

### 6.2 What the dump looks like

A login screen (this is the real test fixture `tests/fixtures/login.xml`, trimmed):

```xml
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" ...>
    <node class="android.widget.TextView" text="Welcome back"
          resource-id="com.example.app:id/title"       bounds="[60,200][1020,300]" />
    <node class="android.widget.EditText" content-desc="Phone number"
          resource-id="com.example.app:id/phone_input" bounds="[60,400][1020,520]"
          clickable="true" focused="true" />
    <node class="android.widget.EditText" password="true"
          resource-id="com.example.app:id/mpin_input"  bounds="[60,560][1020,680]" clickable="true" />
    <node class="android.widget.Button" text="Login" enabled="false"
          resource-id="com.example.app:id/btn_login"   bounds="[60,800][1020,920]" clickable="true" />
    <node class="android.widget.TextView" text="Help"
          resource-id="com.example.app:id/link"        bounds="[60,1000][500,1060]" clickable="true" />
    <node class="android.widget.TextView" text="Terms of service"
          resource-id="com.example.app:id/link"        bounds="[580,1000][1020,1060]" clickable="true" />
  </node>
</hierarchy>
```

### 6.3 Turning XML into elements

`parse()` walks every `<node>` and builds an `Element`:

```
Element(index, cls, text, rid, desc, bounds,
        clickable, enabled, checked, focused, scrollable, password, package)
```

- **bounds → center.** `[60,800][1020,920]` → center `(540, 860)`. The center is where a
  tap will land.
- **Zero-size nodes are dropped** (invisible layout leftovers).
- **`index`** is just the element's position in this list, so `observe` can label it
  `[4]` and you can say `tap '#4'`.

### 6.4 What Claude actually sees — `observe`

Most nodes are empty layout boxes. `observe` shows only the *interesting* ones —
anything with text, a label, or that can be tapped, scrolled, typed into or checked
(switches and checkboxes show `checked` / `unchecked`):

```
$ ./tnazar observe
screen: com.example.app  8 elements (7 shown)
[1] TextView id=title text="Welcome back" @540,250
[2] EditText id=phone_input desc="Phone number" @540,460 tap focused
[3] EditText id=mpin_input @540,620 tap password
[4] Button id=btn_login text="Login" @540,860 tap disabled
[5] TextView id=link text="Help" @280,1030 tap
[6] TextView id=link text="Terms of service" @800,1030 tap
[7] Switch id=remember_me @960,1180 tap unchecked
```

This compact text is the agent's "eyesight". It is cheap (a few hundred tokens), exact
(no guessing pixels from an image), and carries state a screenshot can't show clearly
(`disabled`, `password`, `focused`, `unchecked`). The full XML is also saved to `runs/<name>/dumps/`
and to `.tnazar/last-screen.xml`, so `#index` taps can reuse it without a new dump.

Screenshots are still taken — as **evidence for humans**, not as the agent's input.

---

## 7. Layer 3 — Finding an element (selectors, not XPath)

*File: `teesri_nazar/screen.py` → `Selector`, `find()`, `stable_selector()`*

### 7.1 Why not XPath?

Tools like Appium usually locate elements with XPath over this same XML, e.g.
`//android.widget.FrameLayout/android.widget.Button[2]`. That is powerful, but it is
**brittle**: it encodes the *layout structure*, so wrapping a button in one extra layout
breaks the test even though nothing a user sees has changed.

Teesri Nazar instead matches on **what the element is**, not **where it sits in the
tree**. A selector is just up to three attributes:

```python
Selector(id="btn_login")          # resource-id
Selector(desc="Navigate up")      # accessibility label
Selector(text="Pay now")          # visible text
Selector(text="Pay", contains=True)   # partial text
Selector(id="link", text="Help")  # combine to disambiguate
Selector(text="Delete", nth=1)    # the 2nd match
```

The location is never queried — it is **computed**: find the element in the flat list,
take its `bounds`, compute the center, tap there.

### 7.2 How matching works

`find_all()` scans the element list and keeps every element that satisfies **all** the
attributes given:

- **id:** `btn_login` matches `com.example.app:id/btn_login` (short form is fine).
- **text / desc:** whitespace-collapsed, case-insensitive, and curly quotes (`’ “`) equal
  straight ones (`' "`). Exact by default; substring with `~` / `--contains`.
- **Forgiving fallback for actions:** for `tap`, `type --into` and `wait`, a
  bare text selector is tried, in order, as exact text, exact content-desc (Compose apps
  often put the label only there), partial text, partial content-desc — a label typed
  loosely (`"Pay now"`) often lives inside a longer string.
- **Checks are strict:** `check "Pay now"` matches exact text, then exact content-desc, and
  never falls back to a partial match, so a check can't pass on a different label that
  merely contains the words. Write `check '~Pay'` to mean "contains". The same goes for
  `--gone`: `check Save --gone` is not fooled by a new "Couldn’t save? Try again" label.

### 7.3 The priority rule: id → desc → text → coordinates

When the agent writes a target, it is told (by `CLAUDE.md`) to prefer, in order:

| Rank | Target | Why |
|---|---|---|
| 1 | `id=btn_pay` | Set by developers, doesn't change with copy or language. |
| 2 | `desc="Navigate up"` | Accessibility labels are fairly stable, and icons often have no text. |
| 3 | `"Pay now"` | Visible text — readable, but changes with copywriting / localisation. |
| 4 | `540,1860` | Raw pixels — last resort. Breaks on a different screen size or layout. |

### 7.4 Turning `#index` into a durable selector

When the agent taps `'#4'` from the last `observe`, `tnazar` does **not** record "index 4"
or "540,860" — those would mean nothing on a replay. `stable_selector()` picks the most
durable selector that **uniquely** identifies that element on that screen:

```
candidates for an element:  id  →  desc  →  text  →  id+text
pick the first one that matches exactly one element on screen;
otherwise the first one that matches it, plus nth = its position among matches.
```

From the login screen above:

| Tapped | Recorded selector | Why |
|---|---|---|
| `#4` (Login) | `id=btn_login` | id is unique → best choice. |
| `#3` (MPIN box) | `id=mpin_input` | unique id; its text is never used because it's a password field. |
| `#5` (Help) | `"Help"` | `id=link` is shared by Help *and* Terms, so id alone is ambiguous → falls through to text. |

That one function is why a session done "by hand" by the agent is replayable later.

---

## 8. Layer 4 — Acting on the phone

*Files: `teesri_nazar/cli.py` (commands), `teesri_nazar/device.py` (device actions)*

Every action follows the same path:

```mermaid
flowchart LR
    A["./tnazar tap 'Login'"] --> B["parse_target()<br/>text / ~partial / id= / desc= / #n / x,y"]
    B --> C["resolve()<br/>dump screen, find element<br/>(or wait for it with --wait)"]
    C --> D["element.center → (540, 860)"]
    D --> E["adb shell input tap 540 860"]
    E --> F["log event:<br/>selector + xy + screen size"]
    F --> G["print: tapped 'Login' at 540,860"]
```

| Command | What it does under the hood |
|---|---|
| `tap` | Find → center → `input tap`. `--long` = a swipe that doesn't move for 800 ms. A target `x%,y%` (for an element with no label) is converted to pixels on the current screen and recorded with its size, so replay on another phone lands on the same spot. |
| `type` | Optionally tap the field (`--into`), clear it (`--clear`: move cursor to end, then backspace N times), then `input text`. Spaces and shell-special characters are escaped. ASCII only. Short all-digit values (≤ 8 digits: OTPs, PINs) go one digit at a time, because segmented PIN boxes drop digits that arrive in one burst. |
| `key` | Named key → Android keycode (`back`=4, `enter`=66, …) → `input keyevent`. |
| `scroll` | Swipe 30% of the area from the middle. `down` *reveals what's below* (finger moves up). `--to X` repeats until X is visible, and stops early if the screen stops changing (end of list). `left`/`right` swipe inside the screen's horizontal scroller (a tab bar, a carousel) when there is one, so plans need no coordinates. |
| `swipe` | Raw coordinates. Screen size is recorded so replay can scale it. |
| `launch` | Resolve the app's launcher activity and start it. Plain `launch` resumes the app where it was; `--restart` force-stops first, `--fresh` also clears data, and both start with `NEW_TASK\|CLEAR_TASK` so activities left in the task (including other packages' ones) are dropped. |
| `open-url` | Fire a VIEW intent — for deep links. |
| `wait` | Poll the screen every 0.4 s until the element appears (or with `--gone`, disappears). |

**Waiting instead of sleeping.** Apps load at different speeds. Fixed `sleep 3` is either
too slow or too short. `wait` / `tap --wait 10` / `check --timeout` all poll the UI tree
until the condition is true or the timeout expires.

**Animations off.** `start` sets the phone's three animation scales to 0 so screens settle
immediately and dumps don't catch half-drawn screens. The old values are saved in
`run.json` and restored by `stop` (and by a Claude Code `SessionEnd` hook if a session
ends with a run still open). They are also saved on the device itself, in
`/data/local/tmp/tnazar-animations`: if a run never restored them (device unplugged,
host killed, a different `TNAZAR_HOME`), the next `start` reads that file instead of
taking 0 as the baseline. Restoring deletes the file.

**Secrets never touch the log.** `type --env APP_MPIN` reads the value from `.env`; the
event records `"text": null, "secret": true, "env": "APP_MPIN"` — the name, never the value.
Typing into a `password="true"` field is treated as secret automatically.

---

## 9. Layer 5 — Verifying (checks)

*File: `teesri_nazar/cli.py` → `cmd_check`*

A test without assertions is just a demo. Every expected result becomes a **check**.

### UI checks (automatic)

```bash
./tnazar check "Payment successful"                 # visible within 5 s
./tnazar check id=loader --gone --timeout 15        # disappears
./tnazar check id=btn_login --enabled false         # state
./tnazar check id=tnc_checkbox --checked true
```

`tnazar` polls the UI tree until the element is found (or gone), then compares the
`enabled` / `checked` flags. A dump that fails mid-animation is retried until the timeout;
only a screen that never dumps is an error. Every check — pass or fail — **saves a
screenshot** when it can: on screens that block capture (`FLAG_SECURE`) the check and case
result are still recorded, with a warning instead of the shot. A failing check also saves
the full XML dump, so you can see exactly what *was* on screen.

### DB / Figma / API checks (agent-verified, tnazar-recorded)

`tnazar` doesn't connect to databases or Figma itself. Claude does the verification with
its own tools and records the outcome:

```bash
./tnazar check --result pass --kind db --what "txn row status = SUCCESS" \
    --detail "select status from txn where id='T123' -> SUCCESS"
./tnazar check --result fail --kind figma --what "CTA copy matches design" \
    --detail "Figma: 'Pay now', app: 'Pay Now'" --shot
```

Rules from the playbook: DB access is **read-only** and **test environment only**; the
query and its result go in `--detail` so a reviewer can reproduce it.

### Notes

`./tnazar note "Amount field accepts 0" --kind bug` records an observation, bug,
question or risk. Bugs are collected into their own section of the report.

---

## 10. Layer 6 — Recording a run

*File: `teesri_nazar/session.py`*

Everything that happens is appended to an **event log**. Nothing is ever edited in place.

```
runs/login-smoke/
├── run.json        ← metadata: device, model, Android version, app + version,
│                     the original request, start/finish time, saved animation values
├── events.jsonl    ← one JSON line per thing that happened (the source of truth)
├── shots/          ← screenshots (every check, every failure, explicit `shot`)
├── dumps/          ← UI tree XML (every observe, every failed check)
├── logs/           ← logcat and crash text for failed / crashed cases
├── report.md       ← generated at stop
└── report.html     ← generated at stop — the one to share
```

A few lines of `events.jsonl`:

```json
{"t":"…Z","type":"case_start","case":"TC-01","title":"Login with valid MPIN"}
{"t":"…Z","type":"type","text":null,"secret":true,"env":"APP_MPIN","selector":{"id":"mpin_input"},"xy":[540,620],"screen":[1080,2400],"clear":false,"enter":false}
{"t":"…Z","type":"tap","selector":{"id":"btn_login"},"xy":[540,860],"label":"Login","long":false,"screen":[1080,2400]}
{"t":"…Z","type":"check","kind":"ui","ok":true,"what":"\"Home\" is visible","selector":{"text":"Home"},"shot":"shots/101502-check-pass.png","case":"TC-01"}
{"t":"…Z","type":"tap","ok":false,"target":"Nowhere","long":false,"error":"not found: \"Nowhere\""}
{"t":"…Z","type":"type","text":null,"secret":true,"env":"UI_OTP","by":"dashboard"}
{"t":"…Z","type":"case_end","case":"TC-01","status":"pass","reason":""}
```

Why an append-only log?

- **It is the recording.** Each action stores *both* the selector and the coordinates and
  screen size, which is everything replay needs.
- **It is crash-safe.** If the session dies, everything up to that moment is on disk.
- **Reports are derived, not written.** Re-running `./tnazar report <run>` rebuilds the
  report from the log at any time.

**One active run at a time.** `.tnazar/active.json` points to the current run and device,
so every command (`tap`, `check`, …) knows where to log without being told. It also records
the process that started the run (pid and start time, so a reused pid does not count). A
new `start` or `run` finishes the active run quietly when that process has exited (an agent
driving commands one by one), and refuses with exit `2` while it is still alive (a `tnazar
run <plan>` in progress): two runs on one device take the screen from each other.
`./tnazar stop` still ends it from anywhere.

---

## 11. Layer 7 — Pass / fail and the report

*Files: `teesri_nazar/cli.py` → `_end_case`, `teesri_nazar/report.py`*

### How a case gets its result

The agent wraps steps in `case start TC-01 "title"` … `case end`. At `case end`,
`tnazar` — not the agent — decides the result from the evidence:

```mermaid
flowchart TD
    E["case end"] --> C{"crash or ANR<br/>in logcat for this app?"}
    C -->|yes| F["❌ fail"]
    C -->|no| K{"any failed check?"}
    K -->|yes| F
    K -->|no| H{"any checks at all?"}
    H -->|yes| X{"every expected result<br/>(E1, E2…) proven?"}
    X -->|yes| P["✅ pass"]
    X -->|no| B2["⛔ blocked<br/>(not verified: E2 …)"]
    H -->|no| B["⛔ blocked<br/>(nothing was verified)"]
```

**Requested cases (the coverage ledger).** A run is *declared* once it records `case_plan`
events: `tnazar run` writes one per selected case before the first starts; by hand,
`case plan <id> [title] --expect TEXT…`. When the run ends (`stop`, the end of `run`, a new
`start`, or replay's end) every declared case that never started gets a `not_run` event with
the reason (`run stopped at TC-03: error: …`, `--stop-on-fail after TC-03`, `run stopped
with tnazar stop…`). The summary line then counts every requested case and ends with
`| verified V/N` (V = pass + fail); the report adds `Result: all passed | failed |
incomplete`, where *all passed* also needs no failed check anywhere. A run with no
`case_plan` reports exactly as before, plus "requested cases not declared: completeness
unknown". Rules: `teesri_nazar/coverage.py`; design: `docs/trd-coverage.md`.

- logcat is cleared at `case start`, so a crash found at `case end` belongs to this case.
- The agent **can** pass `blocked --reason "backend down"` or `skipped` explicitly.
- The agent **cannot** mark a case `pass` if a check failed or the app crashed —
  `tnazar` overrides it to `fail` and notes that the tester tried to pass it. Nor while an
  expected result is unproven: it becomes `blocked`, naming it.
- Expected results: `expect` lines in a plan case (or `case plan --expect`) are E1, E2… in
  order; `check … --for E1` links a check to one. A failed linked check fails the case. On
  replay, an expected result proven only by a DB/Figma check is not re-verified, so its case
  is blocked, not green.
- On fail / blocked / crash, an end-of-case screenshot and a logcat dump are attached
  automatically as evidence.

This is a key safety rail: **the AI decides what to check; the tool decides whether it passed.**

### The report

`./tnazar stop` builds `report.md` and a self-contained `report.html` from `events.jsonl`:

- header: app + version, device, Android version, duration, the original request;
- summary tiles: pass / fail / blocked / skipped, checks passed out of total;
- a case table with result and reason;
- per case: every check (✅/❌ with kind and detail), notes, crash logs, screenshots;
- a "Bugs found" section collected from `note --kind bug`.

Finally, Claude reports back in chat: summary line, case table, each failure as
*expected vs. actual*, bugs, what was not tested, and the path to `report.html`.

### Exit codes

Every `tnazar` command, `tnazar run <plan>`, `tnazar flow` and `tnazar replay` use the same codes, so
Claude (and CI) can act on them without reading the output:

| Code | Meaning | What to do |
|---|---|---|
| `0` | OK: the command worked; for `run` / `replay`, every case passed | Go on. |
| `1` | Failed: a check failed, an element was not found, or an adb command failed. For `run` / `replay`, any requested case that did not pass: fail, blocked (e.g. an expected result not verified) or not run | Read the output; for `run`, see the report. |
| `2` | Error: a bad plan line, or a setup problem (no device, app not installed, bad flags, another process's run still in progress on the device) | Fix the line or the setup; nothing was tested. For a run in progress, wait for it or `./tnazar stop` it. |
| `3` | Held: the user sent a dashboard message, so the command was **not run**. For `wait-message`: a message arrived | Read the `>> USER` message and re-plan; for `run`, continue with `--from <case>`. An *info* message (the dashboard already typed a secret) is shown without holding. |
| `4` | `wait A --or B`: B showed up instead of A | Treat as the failure B stands for. Inside a plan this fails the case. |
| `5` | Still paused from the dashboard after 60 s; the command was **not run** | Run `./tnazar wait-resume` until it prints `resumed`, then continue. |
| `130` | Interrupted (Ctrl-C) | — |

`doctor` uses only `0` (a run can start on at least one device; warnings allowed) and `2` (a
blocker: nothing can run until it is fixed). See §5, *Checking the setup*.

---

## 12. The brain — how Claude decides

*Files: `CLAUDE.md`, `.claude/skills/test-feature/SKILL.md`, `.claude/settings.json`*

There is **no AI code inside `tnazar`** (with one optional exception, visual replay in
§14). The intelligence comes from running **Claude Code** in this folder. Claude Code
automatically loads:

| File | Role |
|---|---|
| `CLAUDE.md` | The **playbook**: "you are a senior QA engineer"; the workflow (plan → start → cases → stop → report); how to drive the device; how to verify; the rules. |
| `.claude/skills/test-feature/SKILL.md` | A **skill** that triggers when you ask to test / QA / verify a feature, and points Claude through the same steps. |
| `.claude/settings.json` | **Permissions** (`./tnazar` and `adb devices` may run without prompting) and a **`SessionEnd` hook** that runs `./tnazar stop --if-active` so a run is never left open with animations off. |
| `testdata.yaml` (yours, gitignored) | Test accounts, environment, DB and Figma pointers. Read by Claude, not by `tnazar`. |
| `.env` (yours, gitignored) | Secrets (MPIN, OTP) — referenced only by name via `--env`. |

### What Claude decides vs. what tnazar decides

| Decision | Who |
|---|---|
| Which test cases to run, including missed edge cases (empty input, back mid-flow, retry, boundary amounts, kill & reopen) | 🤖 Claude |
| What to tap next, what to type, when a screen "looks wrong" | 🤖 Claude |
| What each expected result is, and recording it as a check | 🤖 Claude |
| Whether a DB row / Figma frame matches | 🤖 Claude (via its own tools) |
| Which element a selector matches, and where its center is | 🧰 tnazar |
| How long to wait, and whether the element appeared | 🧰 tnazar |
| Which selector to *record* so it can be replayed | 🧰 tnazar |
| Whether the case passed, failed or was blocked | 🧰 tnazar |
| Detecting crashes / ANRs | 🧰 tnazar |
| Keeping secrets out of logs | 🧰 tnazar |

### The agent's inner loop

```mermaid
flowchart LR
    O["👀 observe<br/>read element list"] --> T["🧠 think<br/>where am I? what's next<br/>for this test case?"]
    T --> A["✋ act<br/>tap / type / scroll<br/>by id → desc → text"]
    A --> W["⏳ wait<br/>for the next screen"]
    W --> V["✔️ check<br/>expected result"]
    V --> O
```

Rules the playbook enforces on this loop:

- **Never guess a tap from a screenshot — `observe` first.**
- Observe once, then act (`tap '#12'` reuses the last dump; no extra 2–3 s).
- After anything that navigates, `wait` for the next screen instead of sleeping.
- If something outside the feature breaks (login, network, backend), mark the dependent
  cases **blocked** with the reason — don't report them as feature failures.
- Never print or note passwords, MPINs, OTPs or customer data.

### How Claude reads the outside world

- **PRD / Confluence / Jira / Zephyr** — via the Atlassian MCP.
- **Figma** — via the Figma MCP (`get_screenshot`, `get_design_context`), compared with a
  `./tnazar shot` of the app: copy, element presence, order, obvious colour/spacing issues.
- **DB** — read-only queries against the test environment in `testdata.yaml`; refuses
  anything that looks like production.

---

## 13. One request, end to end

*"Login with the test user and verify the home screen loads."*

```mermaid
sequenceDiagram
    actor U as You
    participant C as Claude Code
    participant T as tnazar
    participant P as Phone (adb)

    U->>C: "Login with the test user and verify home loads"
    C->>C: read CLAUDE.md, testdata.yaml
    C->>U: Plan: TC-01 valid login, TC-02 wrong MPIN, TC-03 back mid-flow… approve?
    U->>C: go ahead
    C->>T: start login-smoke --package com.example.app
    T->>P: app version, animations → 0, clear logcat
    C->>T: case start TC-01 "Valid login"
    C->>T: launch --restart
    T->>P: am force-stop, am start
    C->>T: observe
    T->>P: uiautomator dump
    P-->>T: XML
    T-->>C: [2] EditText id=phone_input … [4] Button id=btn_login … disabled
    C->>T: type "9000000001" --into id=phone_input
    T->>P: input tap 540,460 · input text 9000000001
    C->>T: type --env APP_MPIN --into id=mpin_input
    T->>P: input tap 540,620 · input text ••••
    C->>T: tap id=btn_login
    T->>P: dump → find → input tap 540,860
    C->>T: check "Home" --timeout 15
    T->>P: poll dump every 0.4 s… found · screencap
    T-->>C: PASS [ui] "Home" is visible
    C->>T: case end
    T->>P: logcat crash buffer (none)
    T-->>C: case TC-01: PASS
    Note over C,T: …TC-02, TC-03…
    C->>T: stop
    T->>P: restore animations
    T-->>C: 3 case(s): ✅ 2 pass ❌ 1 fail · report.html
    C->>U: summary table, failure details, bugs, report link
```

---

## 14. Replay — running a recording again

*File: `teesri_nazar/replay.py`*

Because every step was recorded with a selector, a finished run is also a regression test:

```bash
./tnazar replay login-smoke             # same app state
./tnazar replay login-smoke --fresh     # clear app data first
./tnazar replay login-smoke --visual    # also compare screenshots with Claude
```

How it works:

1. Load `runs/login-smoke/events.jsonl`. Create a **new run** `login-smoke-replay-<time>` —
   so a replay gets its own full report.
2. Walk the events in order:
   - **actions** (`tap`, `type`, …) → re-find the element **by its recorded selector**
     (waiting up to `--step-timeout`, default 10 s), then act on its *new* center.
     No selector? Use the recorded coordinates, **scaled** to the new screen size
     (`x × new_width / old_width`).
   - **`key back`** → the recording notes whether the keyboard was open. Back over an open
     keyboard only closes it, so if the keyboard state differs on replay, the back is
     skipped (keyboard already hidden) or an extra back is pressed first (keyboard open
     now but not then).
   - **UI checks** → re-verified for real, with new screenshots.
   - **DB / Figma checks** → can't be re-verified automatically; logged as notes.
   - **secrets** → re-read from the same `$ENV` name on the replaying machine. A secret the
     user typed live from the dashboard (`"by": "dashboard"`) is skipped with a note unless
     its `$UI_…` name is set.
   - **steps that did not run** (`"ok": false`: a tap that found nothing) → skipped.
3. If a step fails, the rest of that case is skipped and the case is marked **blocked**;
   replay moves on to the next case.
4. Exit code `0` only if every case and check passed — usable in CI.

### Visual comparison (`--visual`)

Selectors prove the *same elements* exist; they don't prove the screen *looks* right.
With `--visual`, `visual.py` hands each (original, replay) screenshot pair to Claude in
headless mode:

```bash
claude -p "<compare these pairs…>" --allowedTools Read --output-format json
```

Claude is told to ignore the status bar, clocks, OTPs and other naturally changing
values, and to fail on a different screen, missing/extra elements, wrong text, error
dialogs or broken layout. Each verdict becomes a check with a one-sentence reason. This
uses tokens, so it is opt-in.

---

## 15. The live dashboard

*Files: `teesri_nazar/ui.py`, `teesri_nazar/ui.html`, `teesri_nazar/live.py`*

`./tnazar ui` opens a localhost page with three resizable panes: the **timeline** of the
run (Claude's narration with every step under it and how long it took), a **control** pane
to steer the agent, and the device **screen**, refreshed about every 1.5 s.

```mermaid
flowchart LR
    B["🌐 Browser<br/>127.0.0.1:8765"] -- "poll events / screen" --> S["tnazar ui<br/>(stdlib HTTP server)"]
    S -- "reads" --> E["runs/&lt;name&gt;/events.jsonl"]
    S -- "screencap + dumpsys window" --> D["📱 Device"]
    B -- "message / pause / secret" --> S
    S -- "writes" --> I[".tnazar/inbox.jsonl<br/>.tnazar/paused"]
    S -- "Type it now: input text" --> D
    C["🤖 Claude → ./tnazar tap …"] -- "checks before each device step" --> I
    C -- "type --env UI_OTP" --> S
    W["🤖 wait-message (background)"] -- "wakes Claude on a message" --> I
    C & S -- "one turn at a time" --> L[".tnazar/device.lock"]
    C -- "status" --> A[".tnazar/agent.json<br/>listener.json"]
```

Claude runs one short `tnazar` command per step, so **every device command is a
checkpoint**; between steps, the background `wait-message` is the channel that wakes it.

- **Messages.** Before a device command (`tap`, `type`, `check`, `scroll`, …) runs, unread
  messages are printed as `>> USER (dashboard): …`, recorded as `user_msg` events, and the
  command is **not run** (exit 3), so Claude re-plans with the new instruction. Other commands
  (`say`, `note`, `case`) show pending messages without consuming them, so a chained
  `say … && tap …` still stops at the tap. Messages sent before `start` are dropped.
- **Pause.** While `.tnazar/paused` exists a device command waits up to 60 s (under the
  agent's 2-minute command timeout); if still paused it exits 5 and tells Claude to run
  `./tnazar wait-resume` (repeat until it prints `resumed`). If a message arrived during the
  pause, the command exits 3 instead of 5 when its wait ends; `wait-resume` only waits for resume. The wait is logged as a `hold` event.
- **Fast screen reads.** `uiautomator dump` starts a new process per read (~2 s, unbounded
  while the app is busy). When the device has the UiAutomator2 server, `start` launches it
  (`uia2.py`) and every read is an HTTP call to it (~0.05 s, waiting at most 300 ms for the
  app to settle); waits then poll every 0.15 s. The server and `uiautomator dump` cannot run
  together, so on a dead server tnazar stops it and falls back, with a warning. A server
  error while a window changes (Android 14+: `Cannot set AccessibilityNodeInfo's field
  'mSealed'`) is retried for up to ~3 s first. `stop` shuts the server down.
- **Fail fast.** `wait A --or B` returns at once (exit 4) when B shows up instead of A.
  All exit codes: [§11, Exit codes](#exit-codes).
- **Slow steps.** Any step over 5 s prints `SLOW: <step> took Xs (N screen reads)` as it
  happens and is listed in the report's *Slow steps* table. A screen read costs ~2 s, so
  `tests/test_cli.py::DeviceBudgetTest` pins how many reads each command makes. A dump
  that hangs (the app never goes idle) is cut at 10 s, killed on the device
  (`pkill -x uiautomator`) and retried.
- **Token cost per test case.** The real model usage per case — turns, new tokens, cached context re-read — comes from Claude
  Code's session log for this folder; a case owns the requests since the previous case
  ended. Shown on the case's line in the timeline and in the report's *Tokens* column.
- **Narration and timings.** `./tnazar say "…"` records a `say` event, the timeline heading
  for the steps after it. Every action event carries `dur_ms`; `wait` and `check` add
  `polls` (screen dumps it took). Steps over 5 s are highlighted.
- **Listening while idle.** `./tnazar wait-message` blocks until a message the agent must
  act on arrives (exit 3, after delivering it) or the run ends (exit 0; `--timeout N` gives
  up sooner). Info notes don't wake it. Claude keeps one running in the background while a
  run is open, so a message reaches it in about a second even between steps. Delivery takes
  a lock (`.tnazar/inbox.lock`), so the listener and a command never print the same message
  twice; if a command delivered it first, the listener keeps waiting. A `tnazar run` plan or
  `tnazar flow` also stops before its next step for a message the listener delivered
  (`live.Since`, counted from the moment the plan or flow starts), so the plan and the agent
  never drive the device at the same time. When a new run replaces the one it listens to,
  it exits 0 and says so; the agent starts it again.
- **Agent status.** Two facts, kept apart: `.tnazar/listener.json` has the pid of the
  running `wait-message` (listening while it is alive), and `.tnazar/agent.json` has what the
  agent last did: `got_message` (set on delivery, quoting the message, not an info note) or
  `working` (its last `say`). The control pane shows one line: *Got your message* for 5 s
  after delivery, then *Thinking about your message* until the next `say`, and after 2 min
  without one *No reply … the agent may have stopped*. *Working* shows for 60 s after a `say`;
  then the line falls back to *Listening* or *Not listening right now*. `start` clears both
  files.
- **Secrets.** A value typed in the dashboard stays in the server's memory. With **Type it
  now** (the default) the server types it straight into the field that has focus, because
  PIN and OTP screens expire; the step is logged as a `type` event with `"by": "dashboard"`
  and no value, and Claude gets an *info* message ("already typed: check the screen") that
  is shown but does not stop its next command. Typing and every agent step that can move
  focus (`tap`, `type`, `key`, `scroll`, …) take turns through an flock on
  `.tnazar/device.lock`: two dashboard requests never interleave their digits, a tap never
  moves focus mid-PIN, and a holder that dies frees the lock at once. Each side waits up to
  30 s (a `tap --wait` can hold it that long), then gives up and says *try again*; waits and
  checks don't take the lock. If typing fails, the reason is put in words of its own
  (*adb timed out*, *the device is offline*, …): adb's message quotes the command and the
  device may echo pieces of the value, so none of its text is kept. The failure is a red
  `type` row (`"ok": false`). Without *Type it now*, Claude is told
  the name (`UI_OTP`) and types it with `--env UI_OTP`; `type` fetches it from the server
  with a per-session token (in `.tnazar/ui.json`, mode 600). `tnazar adb` arguments that
  contain any `.env` value or dashboard secret are logged as `••••`. Masking covers printed output
  and saved UI trees, not images: a secret typed into a plain (non-password) field is on the
  device screen, so screenshots can show it.
- **Secure screens.** The server reads `dumpsys window` and, when an on-screen window has
  `FLAG_SECURE`, shows *"Screen hidden by the app"* instead of a black frame. This is a
  capture limit, not a test failure; the timeline keeps going.
- **Safety.** The server binds `127.0.0.1` only, rejects any `Host` header other than
  `127.0.0.1:<port>` / `localhost:<port>` (DNS rebinding), needs the session token for every
  change, and serves files only from inside `runs/`.

Limit: a message reaches Claude while it is running commands or has `wait-message` running.
Otherwise it waits for the next step, and the status line says *Not listening right now*.
Claude still needs a few seconds to read the message and decide; only the delivery is instant.
A step that could not run (a tap that found nothing, a failed adb command) is a red row with
its reason.

---

### Dashboard acceptance list

What the dashboard must do, one checkable line each. Every line has tests
(`tests/test_acceptance.py` maps each ID to them and fails if a line has none, or names a
test that no longer exists), and `scripts/smoke_dashboard.py` checks the ones marked *live*
on a real device.

| ID | The dashboard must… | Live |
|---|---|---|
| DA-01 | deliver your message to a listening agent within about a second (`wait-message` exits 3) | live |
| DA-02 | never let an info note ("already typed") wake the agent or stop its step | live |
| DA-03 | type a secret into the focused field as soon as the agent's step in progress ends | live |
| DA-04 | type two secrets sent together one after the other, never mixed | live |
| DA-05 | keep every part of a secret off disk, out of the HTTP reply and away from the agent | |
| DA-06 | show a step that could not run as a red row with its reason | live |
| DA-07 | keep the status line true: Got → Thinking → Working → Listening / Not listening, never stuck, time only on timed lines | live |
| DA-08 | never draw your message below a step that came after it | live |
| DA-09 | write timeline lines in plain words: ids as words, your words as written, partial matches named, no false "timed out" | live |
| DA-10 | keep a paused run paused: an info note does not turn a pause into a message | |
| DA-11 | stop a plan or flow before its next step for a message the agent got, even through the listener | |
| DA-12 | run a saved route in one command, and name the existing ones when asked for a missing one | live |
| DA-13 | show a `goto` as one plain line, a broken learned route as a red row saying what it expected, and no row for the map's screen reads | live |
| DA-14 | show every requested case once the run ends: a case that never started is a grey row with the reason it did not run | live |

### 15.1 Running a whole plan

*Files: `teesri_nazar/plan.py`, `teesri_nazar/runner.py`*

Driving a test step by step costs a model turn per command, and each turn re-reads the whole
conversation. `tnazar run <plan>` executes a plan file instead: the same commands, written
once, run back to back in one process. Claude writes the plan, the user approves it, and
Claude only looks at the result (or steps in when the user sends a dashboard message).

```
app com.android.settings
flow home
  launch --restart
  wait "Search Settings"
end
case TC-01 "Network & internet opens"
  run home
  tap "Network & internet"
  check "Internet"
```

- **Checked first.** The whole file is parsed and every step's flags are validated by the
  CLI's own parser before anything runs; errors name the line. A case without a `check`
  is rejected (it would end blocked), as is a `check` inside `retry`. So is an `expect`
  with no `check --for` linking it, a `--for` naming an expected result the case does not
  have, and a `--for` inside a flow (a flow's checks are shared by cases).
- **Blocks.** `flow`/`run` reuse steps; `retry N … end` repeats a block until all its steps
  pass (failed attempts are not recorded); `if "<target>" … else … end` reads the screen
  once and never fails the case; `if "A" --or "B" --wait 10` keeps reading until A or B shows
  (or time runs out) and takes the `else` branch unless A is there. Use it to branch:
  `wait A --or B` fails the case when B shows (exit 4).
- **Failures.** A failed `check` is recorded and the case goes on. A failed action ends its
  case (recorded as a failed check naming the line); the run goes on with the next case, or
  stops with `--stop-on-fail`. Cases after an early stop are reported as not run, with why.
- **Dead preconditions.** A case whose failing step was inside a flow it ran (`run login`)
  marks that flow as failing; the next case running it tries again. After two failures in a
  row, every later case running that flow is ended at once as blocked, "precondition failed:
  flow login (failed in TC-01, TC-02)", with no device call. A case where the flow worked
  clears it.
- **Drafts and review.** `plan.parse(..., draft=True)` collects every completeness gap (a
  case with no steps or no check, an `expect` with no `check --for`, a `--for` naming a
  missing E, a `--for` in a flow) in `Plan.gaps` instead of stopping at the first;
  `tnazar plan review` prints each case's expected results with the checks proving them,
  then all gaps (exit 0 ready, 1 not ready, 2 a syntax error). `run` parses strictly, so its
  first error is unchanged.
- **Import.** `tnazar cases import` (`teesri_nazar/intake.py`) reads CSV, TSV (a sheet's
  paste: a row ends once it has all its tabs, so a cell's line breaks survive) or a Markdown
  table; matches columns by header synonyms, or by `--columns` when there is no header;
  keeps IDs (else `TC-01`…), section rows and an account column as comments; splits expected
  results only on line breaks, bullets or `;`; never copies an OTP/MPIN/password column. It
  writes a draft plan that `run` refuses until it is complete.
- **Dashboard.** Pause holds the runner for as long as the user keeps it paused. A message
  stops the run after the current step, marks the case blocked and leaves the run open
  (exit 3); `--from <case>` continues in the same run.

### 15.2 The app map — saved routes

*File: `teesri_nazar/appmap.py`*

Mid-run, the user often asks for a screen ("open settings"). Exploring there costs a
model turn per step, every time. The app map remembers routes instead: each one is a plan
`flow`, kept in `plans/*.plan` (a map file is `app <package>` plus flows; it needs no case).

- `tnazar flows` lists the flows of every map whose `app` is the active run's app, with a
  one-line summary of each (`tap Settings → wait "Log out"`).
- `tnazar flow <name> [--plan FILE]` runs one against the **active** run with the plan
  runner's executor, so `if`, `retry` and nested `run` work and each step goes through the
  normal CLI: recorded, shown in the timeline (after a `say` heading), held by the
  dashboard. It never starts or stops a run, and the run stays open afterwards.
- **Failures.** A failed step stops the flow and exits with that step's code (1, or 4 for
  `wait --or`), naming the line; it does not fail the current case, since the agent decides
  what a broken route means. A `check` in a flow is recorded in the current case as usual,
  and a failed one makes the flow exit 1.
- **Dashboard.** A message stops the flow after the current step (exit 3). Unlike a plan
  run, a pause longer than 60 s hands the turn back (exit 5), like any device command; the
  flow did not finish, so the agent observes before going on.
- **Lookup.** A name found in two maps of the same app is an error (pick one with `--plan`);
  a map that does not parse is skipped with a warning, so one typo never hides the others.

The agent is told (CLAUDE.md) to try `goto` before exploring; flows are the hand-written,
reviewed routes, and the learned map (15.3) covers everything else.

### 15.3 The learned map — routes nobody writes

*Files: `teesri_nazar/ident.py`, `teesri_nazar/navmap.py`, `goto` and `map` in `appmap.py`.
Design: `docs/prd-nav-map.md`; tests: `docs/test-plan-nav-map.md`.*

Hand-written flows only cover the routes someone saved. The learned map remembers every
route the agent walks, so the second time anyone asks for a screen it is one command.

**Which screen is this?** `ident.identify(screen, app)` keeps what stays put on a screen:
element ids, the labels of buttons outside scrolling lists, and the title (a toolbar's text
or description, a `*title` id, or the top label). It drops digits, typed values, list rows,
and other apps' windows (the keyboard, a permission dialog), then hashes every token. Two
reads are the same screen when their titles match and their tokens overlap ≥ 60 %, or when
an untitled screen matches exactly (settings pages share every row id, so only a title can
anchor a fuzzy match). Loading, WebView/Flutter-canvas and other apps' screens are
*transparent*: never stored. `Screen.signature()` is unchanged (scroll and the report use it).

**Learning costs no screen reads.** Commands stamp the screen they already read on their
event (`sid`: `tap`/`type --into` before acting, `wait`/`check` when they pass, `scroll --to`
after its last swipe; `observe` logs a hidden `seen` event) and keep its hashed tokens in
`runs/<run>/screens.jsonl`. At `stop` (or the next `start` for an unstopped run),
`navmap.learn()` reads the run's events:

| Between two reads of different screens | Learned |
|---|---|
| exactly one `tap` with a selector (the element's id when it has a unique one) | a step X → Y; a `scroll --to` that found it is remembered with it |
| a pixel tap; `back` | knowledge only: never replayed |
| two actions, typing (also from the dashboard), a swipe, `adb`, a gap > 60 s | nothing |
| `launch --fresh/--restart` | a step from the start node `@cold` |
| plain `launch`, `open-url` | nothing: the chain breaks |

A step counts once per run however often it is walked, so asking for Passbook ten times adds
no rows. Names come from `goto <phrase>` that missed: the search ends at the passing `check`
that says every word of the phrase; otherwise the check saying most of it wins, and one that
says none of it counts only if the agent did not press Back after it. `map name` binds a
phrase by hand and overrides learned bindings; `map forget` drops anything older than it.

**Untitled pages and templates.** An untitled page is recognised by its menu (hashed short
labels of buttons in its lists, no digits); a read that lacks the title its screen had matches
on the menu only when exactly one stored screen fits. When several buttons of one screen open
the same screen (FAQ articles, order details), it is a template: a name remembers the tap that
opened it, `goto` must end with that tap and is never "already there" on it.

**The store** is `${TNAZAR_MAP_DIR:-~/.tnazar/maps}/<package>/map.jsonl`: append-only JSON
lines, a run learned once, appends under a file lock, a torn last line kept apart, unreadable
lines skipped with a warning. Nothing in it is screen text: tokens are hashed, and selectors or
labels with digits, `@`, currency or a `.env` value are not stored (the step stays as
knowledge). `TNAZAR_MAP=off` turns learning and `goto` off; `start` says which.

**`goto <phrase>`** (exit codes in `tnazar goto --help`; a phrase with an account number, email
or amount is never stored):

1. A saved flow whose name matches the phrase runs instead.
2. One read; the screen is found in the map plus what this run learned so far.
3. Miss → exit 2 with "did you mean" for close names (never used on its own) and the screen
   listing, so exploring from there costs no extra read or turn. Unknown or transparent
   start screen → exit 2, no `back` presses. A phrase bound to two screens → exit 2.
4. Fewest steps from the current screen over usable steps: a selector, not money-moving
   (pay, transfer, confirm, delete, log out … in its label, recomputed on every load; or into a
   password screen or one titled confirm / checkout / enter PIN, decided when the screen is
   read), not failed in this run, not retired (its last two results on this app version
   failed). `--dry-run` prints the route.
5. Each step taps on the screen just read, then polls until the screen *is* the step's
   target, or fits it without fitting the screen it left (a page the map holds twice, with
   and without its title). The screen it started from means "still on the way"; another screen holding for
   1.5 s means the step went wrong. A wrong step stops with exit 1, prints the screen, and is
   not tried again in this run; the way the agent then finds is learned and preferred.
6. On arrival it prints the screen like `observe` (and refreshes `last-screen.xml`), so the
   agent's next step needs no `observe`. Each step is an ordinary `tap` event (replay re-runs
   it); a `nav` event records the outcome; the dashboard holds it between steps (3, 5).

**Observability.** Every `goto` is a `nav` event (asked for, outcome, steps, time, why it
broke); the run report has a *Navigation map* section listing them with "N of M arrived by
the map" and what the run taught; `stop` prints the same line.

`tnazar map` lists the screens, names and usable steps (`--edges` every step and why one is
not used); `map audit` prints every readable string stored; `map compact` folds the file.

### 15.4 One driver per device, across homes and machines

Two teams, a smoke script beside an agent, or two laptops on one device farm each have their
own `TNAZAR_HOME`, so the per-home `active.json` cannot see each other. `start` therefore
writes the owner on the device itself (`/data/local/tmp/tnazar-owner`: host, home, run,
time) and refuses while another home's run still holds it: on the same machine, while that
home still tracks the run as active; from another machine, for 12 hours. `stop` clears it.
An abandoned owner is taken over with a line saying whose run it was; `--take-over` forces
it; `replay` checks and claims it too. Found when a smoke script started on an emulator another
run was driving.

## 16. Design decisions and trade-offs

| Decision | Why | Cost |
|---|---|---|
| **AI as the tester, tool as the hands** | Planning and judgement are where AI helps; precise, repeatable device I/O and pass/fail rules belong in deterministic code. | Two moving parts to understand. |
| **ADB + UIAutomator, no Appium required** | Nothing to install on the phone, no app changes, works with any installed app. If Appium's UiAutomator2 server is already installed, a run uses it for screen reads (~0.05 s instead of ~2 s) and stops it at the end; without it, `uiautomator dump`. | Limited to what the accessibility tree exposes (§18). |
| **Python standard library only** | `git clone` and run. No `pip install`, no version conflicts; works on Python 3.9+. | Some things are hand-rolled (dotenv loading, HTML report). |
| **Text element list, not screenshots, as the agent's eyes** | Exact ids, text and state; cheap in tokens; no pixel guessing. | Can't "see" purely visual problems — hence Figma checks and `--visual`. |
| **Attribute selectors, not XPath** | Survive layout refactors; readable (`id=btn_pay`). | Need ids/labels to exist; fall back to text otherwise. |
| **Record selector + coordinates + screen size** | Replay can re-find elements, and still has a scaled fallback. | Slightly larger logs. |
| **One dump per `observe`, reuse via `#index`** | A dump costs 2–3 s; the agent shouldn't pay it twice for one decision. | `#index` is only valid until the screen changes. |
| **Poll, don't sleep** | Robust to slow and fast devices alike. | Each poll is a dump; long timeouts mean many dumps. |
| **Animations off during a run** | Fewer half-rendered dumps, faster runs. | Changes a device setting — restored on stop and by the session-end hook. |
| **tnazar derives pass/fail** | The agent can't talk itself into a pass. | The agent must record a check for every expectation, or the case is *blocked*. |
| **Append-only JSONL** | Crash-safe; the log *is* the recording; reports regenerate any time. | No random edits; corrections are new events. |
| **Secrets by env-var name** | Values never appear in commands, logs, recordings or reports. | Replaying machine needs the same `.env`. |

---

## 17. How the tool itself is tested

*Files: `tests/`, `.github/workflows/tests.yml`*

A testing tool that needs a phone to be tested is hard to trust in CI. So:

- **`tests/fakedevice.py`** is a scripted fake phone. `FakeApp` describes screens as lists
  of nodes and what tapping a node or pressing a key does; `FakeAdb` answers the same adb
  calls `tnazar` makes — serving the current screen as a UIAutomator XML dump and
  recording every command — and the clock is patched so waits finish instantly.
- Unit tests cover the screen parser and selectors, device helpers, run storage, every
  CLI command end to end, the case-result rules, report generation, and replay
  (faithful replay, regression detection, secret handling). Newer suites cover the
  dashboard and its server (`test_live.py`), fast reads and their fallback
  (`test_uia2.py`), token cost per case (`test_usage.py`) and plan files
  (`test_plan.py`: parsing, validation and running). `DeviceBudgetTest` pins how many
  screen reads each command makes.
- **GitHub Actions** runs the suite on Python 3.9 and 3.13 on every push and PR — no
  Android SDK needed.

```bash
python3 -m unittest discover -s tests
```

---

## 18. Limits

- **Jetpack Compose** screens show no `resource-id` unless the app enables
  `testTagsAsResourceId`; selectors fall back to labels and text.
- **Flutter, WebView and canvas/game** screens expose little or nothing in the
  accessibility tree, so there is little to find.
- **Secure screens** (`FLAG_SECURE`, e.g. MPIN, card details) come out black in
  screenshots. The element tree still works.
- **Typing is ASCII-only** (`adb input text`): no ₹, Hindi or emoji.
- **One device, one active run at a time.**
- **Dashboard messages wait while nothing listens** — when Claude's turn has ended and no
  `wait-message` runs, they are delivered at its next command.
- **DB / Figma checks are not re-verified on replay** — they appear as notes.

---

## 19. Code map

```
tnazar                      entry script: puts the repo on sys.path, calls cli.main()
teesri_nazar/
├── adb.py        Layer 1   find adb, pick the device, run commands with timeouts
├── screen.py     Layer 2–3 read + parse the UI tree; Element, Screen, Selector;
│                           find / find_all / stable_selector
├── uia2.py       Layer 2   fast reads via the UiAutomator2 server (start/stop, fallback)
├── device.py     Layer 4   tap, type (escaping), keys, swipe/scroll vectors, screenshot,
│                           launch, clear data, deep links, animations, logcat, crashes,
│                           wait_for (polling)
├── cli.py        Layer 4–5 argument parsing and every `tnazar` command's handler
├── targets.py    Layer 3   targets as typed ("Text", ~partial, id=…, #index, x,y) → on screen
├── judge.py      Layer 7   ending a case: the pass/fail rules, evidence for failures
├── env.py                  .env loading; hiding secret values in commands, output, UI trees
├── commands.py             every command and the event it records (one list for all modules)
├── errors.py               exceptions the CLI turns into exit codes
├── session.py    Layer 6   run folder, run.json, append-only events.jsonl, artifacts,
│                           the active-run pointer
├── report.py     Layer 7   events → cases → summary → report.md + report.html
├── replay.py               re-run a recording by selector, re-check UI checks
├── live.py                 CLI side of the dashboard: inbox, pause, runtime secrets
├── ui.py / ui.html         `tnazar ui`: localhost server + the three-pane page
├── usage.py                token cost per test case, from the Claude Code session log
├── plan.py                 plan files: parse and validate (`tnazar run`)
├── ident.py                screen identity for the learned map (same screen across data and scroll)
├── navmap.py               the learned map: learn from a run's events, store, fold, route
├── runner.py               run every case of a plan; hand back on a dashboard message
├── appmap.py               `tnazar flow` / `flows` (saved routes), `goto` / `map` (learned routes)
└── visual.py               optional screenshot comparison via headless Claude
CLAUDE.md                   the tester playbook Claude follows
.claude/skills/test-feature the "test a feature" skill
.claude/settings.json       permissions + SessionEnd safety hook
examples/settings.plan      an example plan (your own plans go in plans/, gitignored)
tests/                      unit tests + fakedevice.py (a fake phone for CI)
```

**If you read only three files**, read `CLAUDE.md` (how the tester thinks),
`teesri_nazar/screen.py` (how it sees), and `_end_case` in `teesri_nazar/cli.py`
(how it judges).
