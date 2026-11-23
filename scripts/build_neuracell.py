"""Build custom_components/ambientika/neuracell.py from the Ambientika MQTT Bridge.

    python scripts/build_neuracell.py path/to/bridge.py custom_components/ambientika/neuracell.py

The NeuraCell-X controller is taken over line by line, so both can be compared;
only the storage (a file in the bridge) and the logger are exchanged.
"""

import sys

src = open(sys.argv[1], encoding="utf-8").read().split("\n")


def cut(start_marker, end_marker):
    """Return the lines from the one starting with start_marker up to end_marker."""
    a = next(i for i, line in enumerate(src) if line.startswith(start_marker))
    b = next(i for i, line in enumerate(src) if i > a and line.startswith(end_marker))
    return "\n".join(src[a:b]).rstrip() + "\n"


helpers = (
    cut("def _truthy(", "def _topic_match(")
    + "\n\n"
    + cut("def _controller_ventilating(", "# -----")
)
ctrl = cut("class NeuraCellXController:", "# -----")


def rep(text, a, b, n=1):
    """Replace a text that must be there exactly n times."""
    assert text.count(a) == n, (text.count(a), a[:60])
    return text.replace(a, b)


ctrl = rep(
    ctrl,
    """        try:
            with open(NEURACELL_STATE_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
""",
    """        raw = self.bridge.nc_load()
        if raw is None:
            return
""",
)
ctrl = rep(
    ctrl,
    """            payload = json.loads(data)
            payload["ts"] = now
            d = os.path.dirname(NEURACELL_STATE_FILE)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = NEURACELL_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, NEURACELL_STATE_FILE)
""",
    """            payload = json.loads(data)
            payload["ts"] = now
            self.bridge.nc_save(payload)
""",
)
ctrl = ctrl.replace("NEURACELL_STATE_FILE", '"the storage of Home Assistant"')
ctrl = rep(
    ctrl,
    "if self.cfg.radon_meter_topic and not _topic_match(self.cfg.radon_meter_topic, topic):",
    "if not self.bridge.nc_is_meter(topic):",
)
ctrl = rep(
    ctrl,
    'if not isinstance(topic, str) or not topic.endswith("/state"):',
    "if not isinstance(topic, str):",
)
head = '''"""NeuraCell-X: radon protection and dew-point control.

Radon protection has priority: while a radon alarm is active every unit runs in
Intake (fresh-air overpressure). A dew-point block switches the selected units
Off while ventilating would bring moisture in. When neither is active any more,
every unit gets back the mode it had before.

The controller below is the one of the Ambientika MQTT Bridge (bridge.py,
class NeuraCellXController), taken over line by line so that both behave the
same and can be compared. Where the bridge speaks of an MQTT topic of a radon
meter or of the Taupunktsteuerung, this integration passes the entity ID of the
Home Assistant entity that delivers the value; "retained" stands for a value
that was not reported live. Only the storage and the logger differ. The file is
built by a script from bridge.py and not edited by hand.

This module has no Home Assistant imports.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any, Optional

from ambientika_py import FanSpeed, HumidityLevel, OperatingMode

from .compat import COMPAT_ENUM_MEMBERS, is_compat_member
from .const import LOGGER as log

# "Zuluftmodus" (supply air / fresh-air overpressure) == OperatingMode.Intake.
RADON_PROTECTION_MODE = OperatingMode.Intake
# Dew-point "do not ventilate" == switch the unit off.
DEWPOINT_BLOCK_MODE = OperatingMode.Off
# Fallback ventilation: the dew-point block holds the units Off, but neither the
# Taupunktsteuerung nor the radon meter is connected - nobody watches the room.
DEWPOINT_FALLBACK_MODE = OperatingMode.ManualHeatRecovery
# The 20-minute timed extraction: an action with an end, never a baseline.
TIMED_MODE = OperatingMode.TimedExpulsion
# Baselines kept over a restart are ignored when older than this (seconds), and
# their time stamp is refreshed this often while protection is active.
NEURACELL_STATE_MAX_AGE = 7 * 24 * 3600
NEURACELL_STATE_REFRESH_S = 12 * 3600
# After a start, kept baselines are not restored during this time, so the
# radon and dew-point inputs can report first (seconds).
STARTUP_GRACE_S = 90.0
# After a restore the cloud may report the protection mode for a short while.
RESTORE_STALE_WINDOW_S = 120.0
# A radon value counts for this long (seconds).
RADON_VALUE_MAX_AGE_S = 1800.0
# The Ambientika radon meter reports 0 right after it starts; ignored for this long.
RADON_METER_BOOT_IGNORE_S = 180.0
RADON_METER_FORGET_S = 7 * 24 * 3600.0
RADON_METER_LIST_REFRESH_S = 3600.0


'''
open(sys.argv[2], "w", encoding="utf-8").write(head + helpers + "\n\n" + ctrl)
