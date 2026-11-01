"""Every tnazar command and the event it records, in one place.

Other modules derive their lists from here (which commands a dashboard pause holds, which
events are timed, what a plan may contain, what replay re-runs), so adding a command is one
edit, not seven.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str            # as typed: `open-url`
    event: str | None    # the event type it records: `open_url` (None: records nothing)
    device: bool = False  # acts on the device: held by a dashboard pause or message
    action: bool = False  # changes the app's state: counted as a step in reports, re-run by replay
    in_plans: bool = False


COMMANDS = (
    Command("launch", "launch", device=True, action=True, in_plans=True),
    Command("open-url", "open_url", device=True, action=True, in_plans=True),
    Command("tap", "tap", device=True, action=True, in_plans=True),
    Command("type", "type", device=True, action=True, in_plans=True),
    Command("key", "key", device=True, action=True, in_plans=True),
    Command("scroll", "scroll", device=True, action=True, in_plans=True),
    Command("swipe", "swipe", device=True, action=True, in_plans=True),
    Command("adb", "adb", device=True, action=True, in_plans=True),
    Command("wait-online", "wait_online", device=True, action=True, in_plans=True),
    Command("wait", "wait", device=True, in_plans=True),
    Command("check", "check", device=True, in_plans=True),
    Command("shot", "shot", device=True, in_plans=True),
    Command("observe", None, device=True, in_plans=True),
    Command("say", "say", in_plans=True),
    Command("note", "note", in_plans=True),
    Command("case", None),
    Command("start", None),
    Command("stop", None),
    Command("status", None),
    Command("report", None),
    Command("runs", None),
    Command("replay", None),
    Command("run", None),
    Command("plan", None),   # read-only: a plan file's review
    Command("cases", None),  # writes a draft plan file: never touches the device
    Command("flow", None, device=True),  # its steps record their own events
    Command("flows", None),
    Command("goto", None, device=True),  # like flow: its taps record their own events
    Command("map", None),
    Command("ui", None),
    Command("devices", None),
    Command("doctor", None),  # read-only setup check: never held, safe during a run
    Command("packages", None),
    Command("wait-resume", None),
    Command("wait-message", None),
)

# Commands a dashboard pause or an unread message holds back.
DEVICE_COMMANDS = frozenset(c.name for c in COMMANDS if c.device)
# Commands that change the app's state (and may move focus): the dashboard waits for them before typing.
ACTION_COMMANDS = frozenset(c.name for c in COMMANDS if c.action)
# Events that record how long their command took (`dur_ms`).
TIMED_EVENTS = frozenset(c.event for c in COMMANDS if c.device and c.event)
# Events of commands that change the app's state: report step counts; replay re-runs them.
ACTION_EVENTS = frozenset(c.event for c in COMMANDS if c.action)
# What a plan file may contain as a step, and what it may not.
PLAN_STEPS = frozenset(c.name for c in COMMANDS if c.in_plans)
NOT_IN_PLANS = frozenset(c.name for c in COMMANDS if not c.in_plans)
# Events that only narrate or record the user's input: replay skips them.
NARRATION_EVENTS = frozenset({"note", "say", "user_msg", "hold", "if", "io",  # "io": logged by older runs
                              "seen", "nav",  # the navigation map's: a screen read, a goto's outcome
                              "case_plan", "not_run",  # the requested cases: copied up front, not steps
                              "scope"})  # a requirements plan's claim: copied up front, judged on new evidence
