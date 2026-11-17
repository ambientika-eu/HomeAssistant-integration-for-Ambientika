"""Constants for ambientika."""

from enum import StrEnum
from logging import Logger, getLogger


LOGGER: Logger = getLogger(__package__)

NAME = "Ambientika"
DOMAIN = "ambientika"
VERSION = "1.4.0"

DEFAULT_HOST = "https://app.ambientika.eu:4521"  # This is the default from ambientika_py. I am not aware of other values yet.

# Seconds between two readings of the status of every unit. All entities of a
# unit share that one reading.
UPDATE_INTERVAL = 30
# Seconds the integration waits for one answer of the Ambientika cloud.
REQUEST_TIMEOUT = 20
# How many units are asked for their status at the same time.
STATUS_REQUESTS_AT_ONCE = 10

# ORDERED_NAMED_FAN_SPEEDS = [name for name, _ in FanSpeed.__members__.items()]
# ORDERED_NAMED_HUMIDITY_LEVELS = [name for name, _ in HumidityLevel.__members__.items()]


class FilterStatus(StrEnum):
    """The filter status of the device."""

    Bad = "bad"
    Medium = "medium"
    Good = "good"


class AirQuality(StrEnum):
    """The air quality of the device."""

    VeryGood = "very_good"
    Good = "good"
    Medium = "medium"
    Poor = "poor"
    Bad = "bad"
