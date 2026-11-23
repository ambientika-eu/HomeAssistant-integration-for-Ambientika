"""What is shown for a unit, worked out from its status packets.

The same rules as in the Ambientika MQTT Bridge, so both show the same values:

* Temperature and humidity pass a plausibility check: a single impossible
  reading is held back, a saturated humidity sensor (above 100 %) reads 100 %.
* Averaged temperature and humidity: the unit reverses its airflow about every
  half minute, so its sensor sees room air and incoming air in turn. The
  averaged values carry the time-weighted mean over a sliding window, with an
  optional hysteresis.
* Options that name single units ("name=value, name=value").

This module has no Home Assistant imports.
"""

from __future__ import annotations

from logging import Logger
import math
import time
import unicodedata

# Plausibility of the two measured values. Values outside the hard limits are
# never shown. A humidity reading below HUMIDITY_LOW_SUSPECT is shown only if
# dry air (below HUMIDITY_LOW_CONFIRM) was already seen: among the previous
# HUMIDITY_LOW_READINGS readings, or a shown dry reading within
# HUMIDITY_LOW_WINDOW_S. Meanwhile the last plausible value stands in, for at
# most PLAUSIBLE_HOLD_S; after that the value is unknown.
HUMIDITY_PLAUSIBLE = (1, 100)
HUMIDITY_SATURATED = 100
TEMPERATURE_PLAUSIBLE = (-40, 85)
HUMIDITY_LOW_SUSPECT = 20
HUMIDITY_LOW_CONFIRM = 25
HUMIDITY_LOW_READINGS = 3
HUMIDITY_LOW_WINDOW_S = 3600.0
PLAUSIBLE_HOLD_S = 600.0
# A status identical to the one read before is the same packet handed out again,
# unless it has been the same for longer than this.
DUPLICATE_PACKET_MAX_S = 120.0

# Averaged values.
AVERAGE_MIN_POLLS = 20
AVERAGE_MAX_GAP_POLLS = 2
AVERAGE_MIN_COVER_S = 120.0
AVERAGE_WINDOW_RANGE = (1.0, 240.0)  # minutes
TEMPERATURE_AVG_WINDOW_RANGE = (0.0, 240.0)  # minutes, 0 = the general window
HUMIDITY_AVG_HYSTERESIS_RANGE = (0.0, 10.0)  # percentage points
TEMPERATURE_AVG_HYSTERESIS_RANGE = (0.0, 5.0)  # °C

HUMIDITY_SUFFIXES = ("% rh", "% rf", "%rh", "%rf", "%")
TEMPERATURE_SUFFIXES = ("°c", "° c", "°", "c", "k", "grad", "deg")
MINUTE_SUFFIXES = ("minutes", "minute", "min", "m")

_AIR_QUALITY_NUM = {"verygood": 4, "good": 3, "medium": 2, "poor": 1, "bad": 0}
_FILTER_STATUS_NUM = {
    "good": 0,
    "green": 0,
    "ok": 0,
    "clean": 0,
    "medium": 1,
    "yellow": 1,
    "moderate": 1,
    "warn": 1,
    "bad": 2,
    "red": 2,
    "dirty": 2,
    "alarm": 2,
}

# Hyphens and dashes a keyboard may put in for a hyphen read as a space.
_NAME_KEY_SEPARATORS = {ord(c): " " for c in "-_­‐‑‒–—―−"}


def _norm(value) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def air_quality_to_num(value) -> int | None:
    """Air quality as a number, 0 (bad) to 4 (very good); None if unknown."""
    return _AIR_QUALITY_NUM.get(_norm(value))


def filter_status_to_num(value) -> int | None:
    """Filter status as a number, 0 (good) to 2 (bad); None if unknown."""
    return _FILTER_STATUS_NUM.get(_norm(value))


def raw_number(value):
    """Return a measured value as the unit reported it; None unless a finite number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def snake(name) -> str | None:
    """Turn a name of the cloud ("ManualHeatRecovery") into a state key."""
    if not isinstance(name, str) or not name:
        return None
    out = []
    for index, char in enumerate(name):
        if char.isupper() and index and not name[index - 1].isupper():
            out.append("_")
        out.append(char.lower())
    return "".join(out).replace(" ", "_").replace("-", "_")


def name_key(value) -> str:
    """Return a device name or serial number as compared in the per-unit options."""
    text = unicodedata.normalize("NFC", str(value or "")).translate(
        _NAME_KEY_SEPARATORS
    )
    return " ".join(text.split()).casefold()


def parse_unit_values(raw_value, bounds, suffixes, what, unit):
    """Parse a per-unit option of "name=value" entries.

    Entries are separated by commas, semicolons or line breaks. Without "=" in
    an entry, ":" separates. Returns (entries, problems): entries as
    (key, label, value) in the order given, problems as texts for the log.
    """
    entries, problems = [], []
    low, high = bounds
    raw = str(raw_value or "")
    for char in (";", "\r", "\n"):
        raw = raw.replace(char, ",")
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        sep = "=" if "=" in part else (":" if ":" in part else "")
        name, _, num = part.rpartition(sep) if sep else ("", "", "")
        name = name.strip()
        if not name:
            problems.append(f"{part!r} is not of the form name={what} - ignored")
            continue
        num = num.strip()
        for suffix in sorted(suffixes, key=len, reverse=True):
            if num.lower().endswith(suffix):
                num = num[: -len(suffix)].strip()
                break
        try:
            value = float(num)
        except ValueError:
            value = float("nan")
        if not math.isfinite(value):
            problems.append(f"{name!r}: {num!r} is not a number of {what} - ignored")
            continue
        if not low <= value <= high:
            clamped = min(high, max(low, value))
            problems.append(
                f"{name!r}: {value:g}{unit} is outside {low:g}-{high:g} - using {clamped:g}"
            )
            value = clamped
        entries.append((name_key(name), name, value))
    return entries, problems


def parse_bounded_float(raw_value, bounds, suffixes, what, unit, default=0.0):
    """Parse one numeric option; returns (value, problem)."""
    low, high = bounds
    if raw_value is None or isinstance(raw_value, bool):
        return default, None
    num = str(raw_value).strip()
    if not num or num.lower() in ("null", "none"):
        return default, None
    for suffix in sorted(suffixes, key=len, reverse=True):
        if num.lower().endswith(suffix):
            num = num[: -len(suffix)].strip()
            break
    try:
        value = float(num)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value):
        return (
            default,
            f"{str(raw_value).strip()!r} is not a number of {what} - using {default:g}",
        )
    if not low <= value <= high:
        clamped = min(high, max(low, value))
        return (
            clamped,
            f"{value:g}{unit} is outside {low:g}-{high:g} - using {clamped:g}",
        )
    return value, None


def match_unit_entries(option, entries, names, unit, log: Logger) -> dict:
    """Map parsed per-unit entries onto the units, by serial number, else by name.

    ``names`` maps serial number -> name in the app. Returns serial -> value.
    """
    by_serial = {name_key(serial): serial for serial in names}
    by_name: dict = {}
    for serial, name in names.items():
        by_name.setdefault(name_key(name), []).append(serial)
    result: dict = {}
    set_by: dict = {}
    for key, label, value in entries:
        targets = [by_serial[key]] if key in by_serial else by_name.get(key, [])
        if not targets:
            log.warning(
                "%s: no unit named %r - entry ignored. Units: %s",
                option,
                label,
                ", ".join(sorted(f"{n} ({s})" for s, n in names.items())) or "(none)",
            )
            continue
        for serial in targets:
            if serial in set_by and result[serial] != value:
                log.warning(
                    "%s: %s is listed more than once (%r, %r) - using %g%s",
                    option,
                    serial,
                    set_by[serial],
                    label,
                    value,
                    unit,
                )
            result[serial] = value
            set_by[serial] = label
    return result


class Measurements:
    """Plausibility check, averaging and hysteresis of the measured values."""

    def __init__(self, log: Logger, poll_interval: float) -> None:
        """Start without any reading."""
        self._log = log
        self.poll_interval = max(1.0, float(poll_interval))
        self.window_s = 1800.0
        self.temperature_window_s = 0.0
        self.unit_window_s: dict[str, float] = {}
        self.hysteresis_global = {"humidity": 0.0, "temperature": 0.0}
        self.hysteresis_unit: dict[str, dict[str, float]] = {
            "humidity": {},
            "temperature": {},
        }
        self._plausible_state: dict = {}
        self._last_status: dict = {}
        self._avg_buf: dict = {}
        self._avg_held: dict = {"humidity": {}, "temperature": {}}

    # ----- options -----
    def configure(self, options: dict, names: dict[str, str]) -> None:
        """Take the options; ``names`` maps serial number -> name in the app."""
        log = self._log
        minutes, problem = parse_bounded_float(
            options.get("average_window_min", 30),
            AVERAGE_WINDOW_RANGE,
            MINUTE_SUFFIXES,
            "minutes",
            " min",
            default=30.0,
        )
        if problem:
            log.warning("average_window_min: %s", problem)
        self.window_s = minutes * 60.0

        entries, problems = parse_unit_values(
            options.get("average_window_devices"),
            AVERAGE_WINDOW_RANGE,
            ("min",),
            "minutes",
            " min",
        )
        for text in problems:
            log.warning("average_window_devices: %s", text)
        self.unit_window_s = {
            serial: value * 60.0
            for serial, value in match_unit_entries(
                "average_window_devices", entries, names, " min", log
            ).items()
        }

        minutes, problem = parse_bounded_float(
            options.get("temperature_avg_window_min", 0),
            TEMPERATURE_AVG_WINDOW_RANGE,
            MINUTE_SUFFIXES,
            "minutes",
            " min",
        )
        if problem:
            log.warning("temperature_avg_window_min: %s", problem)
        self.temperature_window_s = minutes * 60.0 if minutes > 0 else 0.0

        for key, bounds, suffixes, what, unit in (
            (
                "humidity",
                HUMIDITY_AVG_HYSTERESIS_RANGE,
                HUMIDITY_SUFFIXES,
                "percent",
                " %",
            ),
            (
                "temperature",
                TEMPERATURE_AVG_HYSTERESIS_RANGE,
                TEMPERATURE_SUFFIXES,
                "degrees",
                " °C",
            ),
        ):
            option = f"{key}_avg_hysteresis"
            value, problem = parse_bounded_float(
                options.get(option), bounds, suffixes, what, unit
            )
            if problem:
                log.warning("%s: %s", option, problem)
            entries, problems = parse_unit_values(
                options.get(f"{option}_devices"), bounds, suffixes, what, unit
            )
            for text in problems:
                log.warning("%s_devices: %s", option, text)
            self.hysteresis_global[key] = value
            self.hysteresis_unit[key] = match_unit_entries(
                f"{option}_devices", entries, names, unit, log
            )
            self._avg_held[key] = {}

    # ----- one status packet -----
    def is_fresh(self, serial: str, status: dict, now: float | None = None) -> bool:
        """Return False if the status is the packet of the reading before."""
        now = time.monotonic() if now is None else now
        try:
            prev = self._last_status.get(serial)
            fresh = (
                prev is None
                or status != prev[0]
                or now - prev[1] > DUPLICATE_PACKET_MAX_S
            )
        except Exception:  # noqa: BLE001
            fresh = True
        if fresh:
            self._last_status[serial] = (dict(status), now)
        return fresh

    def measured(self, serial: str, key: str, value, fresh: bool, now=None):
        """Return (shown value, averaged value) of one measured value."""
        now = time.monotonic() if now is None else now
        shown = self.plausible(serial, key, value, fresh, now)
        state = self._plausible_state.get(serial, {}).get(key)
        held = state is not None and state.get("bad", 0) > 0
        mean = self.average(serial, key, None if held else shown, now)
        return shown, self.hold(key, serial, mean)

    def plausible(self, serial: str, key: str, value, fresh: bool = True, now=None):
        """Return the value to show for a measured value."""
        if value is None:
            return None
        log = self._log
        st = self._plausible_state.setdefault(serial, {}).setdefault(
            key,
            {
                "last": None,
                "t": 0.0,
                "bad": 0,
                "dry": [],
                "dry_t": None,
                "gone": False,
                "out": None,
                "log_t": None,
                "loud": True,
                "sat": False,
                "sat_log_t": None,
            },
        )
        now = time.monotonic() if now is None else now
        if st["bad"] == 0:
            st["loud"] = st["log_t"] is None or now - st["log_t"] >= 3600.0
        note = log.info if st["loud"] else log.debug
        expired = st["last"] is not None and now - st["t"] > PLAUSIBLE_HOLD_S
        if not fresh and st["out"] is not None and st["out"][1] == value:
            # The same packet as last time: the decision is repeated.
            if st["bad"] == 0 or not expired:
                return st["out"][0]
            if not st["gone"]:
                note(
                    "%s of %s: no plausible value for %.0f min - shown as unknown",
                    key,
                    serial,
                    PLAUSIBLE_HOLD_S / 60.0,
                )
            st["gone"] = True
            st["out"] = (None, value)
            return None
        low, high = HUMIDITY_PLAUSIBLE if key == "humidity" else TEMPERATURE_PLAUSIBLE
        try:
            v = None if isinstance(value, bool) else float(value)
        except (TypeError, ValueError):
            v = None
        if v is not None and not math.isfinite(v):
            v = None
        shown_value = value
        if key == "humidity" and v is not None and v > high:
            if not st.get("sat"):
                loud = st.get("sat_log_t") is None or now - st["sat_log_t"] >= 3600.0
                (log.info if loud else log.debug)(
                    "%s %r from %s above %s %% - sensor saturated, shown as %s %%",
                    key,
                    value,
                    serial,
                    high,
                    HUMIDITY_SATURATED,
                )
                if loud:
                    st["sat_log_t"] = now
            st["sat"] = True
            v = float(HUMIDITY_SATURATED)
            shown_value = HUMIDITY_SATURATED
        elif v is not None:
            st["sat"] = False
        ok = v is not None and low <= v <= high
        why = f"outside {low}..{high}"
        if ok and key == "humidity":
            dry = st["dry"]
            window = max(HUMIDITY_LOW_WINDOW_S, 6 * self.poll_interval)
            seen_dry = any(dry) or (
                st["dry_t"] is not None and now - st["dry_t"] <= window
            )
            if v < HUMIDITY_LOW_SUSPECT and not seen_dry:
                ok = False
                why = (
                    f"below {HUMIDITY_LOW_SUSPECT} % without dry air in the last "
                    f"{HUMIDITY_LOW_READINGS} readings or {window / 60.0:.0f} min"
                )
            dry.append(v < HUMIDITY_LOW_CONFIRM)
            del dry[:-HUMIDITY_LOW_READINGS]
            if ok and v < HUMIDITY_LOW_CONFIRM:
                st["dry_t"] = now
        if ok:
            if st["bad"]:
                note(
                    "%s of %s plausible again (%r) after %d held-back value(s)",
                    key,
                    serial,
                    value,
                    st["bad"],
                )
            st.update(
                last=shown_value, t=now, bad=0, gone=False, out=(shown_value, value)
            )
            return shown_value
        if st["bad"] == 0:
            note(
                "%s %r from %s held back (%s) - showing the last plausible value %r meanwhile",
                key,
                value,
                serial,
                why,
                st["last"],
            )
            if st["loud"]:
                st["log_t"] = now
        st["bad"] += 1
        if st["last"] is not None and not expired:
            st["out"] = (st["last"], value)
            return st["last"]
        if not st["gone"] and st["last"] is not None:
            note(
                "%s of %s: no plausible value for %.0f min - shown as unknown",
                key,
                serial,
                PLAUSIBLE_HOLD_S / 60.0,
            )
        st["gone"] = True
        st["out"] = (None, value)
        return None

    def average_window_s(self, serial: str, key: str | None = None) -> float:
        """Effective averaging window of one unit in seconds."""
        own = self.unit_window_s.get(serial, self.window_s)
        if key == "temperature" and self.temperature_window_s > 0:
            own = self.temperature_window_s
        return max(own, AVERAGE_MIN_POLLS * self.poll_interval)

    def average(self, serial: str, key: str, value, now: float | None = None):
        """Record a reading and return the time-weighted mean (None: unknown)."""
        now = time.monotonic() if now is None else now
        interval = self.poll_interval
        window = self.average_window_s(serial, key)
        cap = AVERAGE_MAX_GAP_POLLS * interval
        start = now - window
        buf = self._avg_buf.setdefault(serial, {}).setdefault(key, [])
        try:
            v = None if value is None or isinstance(value, bool) else float(value)
        except (TypeError, ValueError):
            v = None
        if v is not None and not math.isfinite(v):
            v = None
        buf.append((now, v))
        # One record before the window stays as the anchor of the first inside.
        while len(buf) > 1 and buf[1][0] <= start:
            del buf[0]
        num = den = 0.0
        count = 0
        prev_t = None
        last_ok = None
        for t, x in buf:
            span = min(cap, interval if prev_t is None else t - prev_t)
            prev_t = t
            weight = t - max(t - span, start)
            if x is None or weight <= 0:
                continue
            num += x * weight
            den += weight
            count += 1
            last_ok = t
        if count < 2 or den < min(AVERAGE_MIN_COVER_S, window) or den <= 0:
            return None
        if now - last_ok > max(PLAUSIBLE_HOLD_S, AVERAGE_MAX_GAP_POLLS * interval):
            return None
        return round(num / den, 1)

    def hold(self, key: str, serial: str, value):
        """Apply the hysteresis to an averaged value."""
        try:
            hysteresis = float(
                self.hysteresis_unit[key].get(
                    serial, self.hysteresis_global.get(key, 0.0)
                )
            )
        except (TypeError, ValueError):
            hysteresis = 0.0
        if hysteresis <= 0:
            return value
        held_map = self._avg_held[key]
        held = held_map.get(serial)
        if value is None or held is None or abs(value - held) >= hysteresis - 1e-9:
            held_map[serial] = value
            return value
        return held
