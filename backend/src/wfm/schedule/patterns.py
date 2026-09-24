"""Precomputed shift patterns: a template on a date with its breaks and meal placed.

Each pattern is a complete, contiguous activity timeline (On Phone / Paid Break / Unpaid Meal)
aligned to the scheduling grid, so coverage per interval is unambiguous.

Break placement: by default the Shifts-sheet offsets are used exactly. `stagger_slots = n`
additionally allows each break/meal to move ±n grid slots, staying inside the configured
placement windows, in order and without overlap — useful to avoid everyone breaking at once.

Time: the shift starts at its local wall-clock start; every offset and the shift length are
real elapsed time (computed in UTC), so a DST change inside a shift keeps the paid time right.
"""

import itertools
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from wfm.io.agents import ShiftTemplate

ActivityType = Literal["On Phone", "Paid Break", "Unpaid Meal"]

# Placement windows (minutes after shift start) used when stagger is enabled or offsets missing.
WINDOWS = {"break1": (90, 150), "meal": (180, 300), "break2": (330, 420)}


class PatternError(ValueError):
    """A shift template is internally inconsistent or doesn't fit the grid."""


@dataclass(frozen=True)
class Activity:
    kind: ActivityType
    start: datetime  # timezone-aware
    end: datetime

    @property
    def paid(self) -> bool:
        return self.kind != "Unpaid Meal"

    @property
    def minutes(self) -> int:
        return round((self.end.astimezone(UTC) - self.start.astimezone(UTC)).total_seconds() / 60)


@dataclass(frozen=True)
class Pattern:
    key: str  # unique per date, e.g. "s2@120/240/375"
    shift_code: str
    shift_name: str
    start: datetime
    end: datetime
    activities: tuple[Activity, ...]
    on_phone_slots: frozenset[datetime]  # UTC slot starts with on-phone coverage

    @property
    def paid_minutes(self) -> int:
        return sum(a.minutes for a in self.activities if a.paid)

    @property
    def unpaid_minutes(self) -> int:
        return sum(a.minutes for a in self.activities if not a.paid)


def _placements(
    template: ShiftTemplate, stagger_slots: int, interval: int
) -> list[list[tuple[str, int, int]]]:
    """Candidate (name, offset, length) lists for breaks/meal, ordered by start."""
    elapsed = _elapsed_minutes(template)
    wanted: list[tuple[str, int | None, int]] = []
    if template.paid_break_count >= 1:
        wanted.append(("break1", template.break1_start_offset_min, template.paid_break_minutes))
    if template.unpaid_meal_minutes > 0:
        wanted.append(("meal", template.meal_start_offset_min, template.unpaid_meal_minutes))
    if template.paid_break_count >= 2:
        wanted.append(("break2", template.break2_start_offset_min, template.paid_break_minutes))
    if template.paid_break_count > 2:
        raise PatternError(f"{template.shift_code}: more than two paid breaks is not supported")

    options: list[list[int]] = []
    for name, offset, _ in wanted:
        lo, hi = WINDOWS[name]
        if offset is None:  # no workplan rule: every grid point inside the window
            candidates = list(range(lo, hi + 1, interval))
        else:
            candidates = [offset + k * interval for k in range(-stagger_slots, stagger_slots + 1)]
            if stagger_slots:
                candidates = [c for c in candidates if lo <= c <= hi]
        options.append([c for c in candidates if c % interval == 0])
        if not options[-1]:
            raise PatternError(
                f"{template.shift_code}: no {name} placement fits the {interval}-minute grid"
            )

    placements = []
    for combo in itertools.product(*options):
        items = [(name, off, length) for (name, _, length), off in zip(wanted, combo, strict=True)]
        ends_ok = all(off + length <= elapsed for _, off, length in items)
        ordered = all(a[1] + a[2] <= b[1] for a, b in itertools.pairwise(items))
        if ends_ok and ordered and all(off > 0 for _, off, _ in items):
            placements.append(items)
    if not placements:
        raise PatternError(
            f"{template.shift_code}: breaks and meal can't be placed without overlap"
        )
    return placements


def _elapsed_minutes(template: ShiftTemplate) -> int:
    start = datetime.combine(date(2000, 1, 3), template.start_time)
    end = datetime.combine(date(2000, 1, 3), template.end_time)
    if end <= start:
        end += timedelta(days=1)
    return round((end - start).total_seconds() / 60)


def check_template(template: ShiftTemplate, interval: int) -> None:
    elapsed = _elapsed_minutes(template)
    if elapsed % interval or template.start_time.minute % interval:
        raise PatternError(
            f"{template.work_plan_id}/{template.shift_code}: "
            f"shift is not aligned to the {interval}-minute grid"
        )
    if elapsed - template.unpaid_meal_minutes != round(template.paid_hours * 60):
        raise PatternError(
            f"{template.work_plan_id}/{template.shift_code}: "
            f"{elapsed} elapsed − {template.unpaid_meal_minutes} "
            f"unpaid meal ≠ {template.paid_hours} paid hours"
        )


def build_patterns(
    template: ShiftTemplate, day: date, tz: ZoneInfo, interval: int, stagger_slots: int
) -> list[Pattern]:
    check_template(template, interval)
    start_local = datetime.combine(day, template.start_time, tzinfo=tz)
    start_utc = start_local.astimezone(UTC)
    elapsed = _elapsed_minutes(template)

    def at(minutes: int) -> datetime:
        return (start_utc + timedelta(minutes=minutes)).astimezone(tz)

    patterns = []
    for items in _placements(template, stagger_slots, interval):
        activities: list[Activity] = []
        cursor = 0
        for name, off, length in items:
            if off > cursor:
                activities.append(Activity("On Phone", at(cursor), at(off)))
            kind: ActivityType = "Unpaid Meal" if name == "meal" else "Paid Break"
            activities.append(Activity(kind, at(off), at(off + length)))
            cursor = off + length
        if cursor < elapsed:
            activities.append(Activity("On Phone", at(cursor), at(elapsed)))
        slots = frozenset(
            (a.start.astimezone(UTC) + timedelta(minutes=m))
            for a in activities
            if a.kind == "On Phone"
            for m in range(0, a.minutes, interval)
        )
        key = f"{template.shift_code}@" + "/".join(str(off) for _, off, _ in items)
        patterns.append(
            Pattern(
                key=key,
                shift_code=template.shift_code,
                shift_name=template.shift_name,
                start=start_local,
                end=at(elapsed),
                activities=tuple(activities),
                on_phone_slots=slots,
            )
        )
    return patterns
