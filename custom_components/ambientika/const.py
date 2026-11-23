"""Constants for ambientika."""

from enum import StrEnum
from logging import Logger, getLogger


LOGGER: Logger = getLogger(__package__)

NAME = "Ambientika"
DOMAIN = "ambientika"
VERSION = "1.6.0"

DEFAULT_HOST = "https://app.ambientika.eu:4521"  # This is the default from ambientika_py. I am not aware of other values yet.

# Seconds between two readings of the status of every unit. All entities of a
# unit share that one reading. The default of the option poll_interval.
UPDATE_INTERVAL = 30
# Seconds the integration waits for one answer of the Ambientika cloud.
REQUEST_TIMEOUT = 20
# How many units are asked for their status at the same time.
STATUS_REQUESTS_AT_ONCE = 10

# Options (Settings > Devices & services > Ambientika > Configure). The same
# options, with the same names and defaults, as in the Ambientika MQTT Bridge.
CONF_POLL_INTERVAL = "poll_interval"
CONF_FAILURE_THRESHOLD = "availability_failure_threshold"
CONF_COMMAND_COALESCE_MS = "command_coalesce_ms"
CONF_AVERAGE_WINDOW_MIN = "average_window_min"
CONF_AVERAGE_WINDOW_DEVICES = "average_window_devices"
CONF_HUMIDITY_AVG_HYSTERESIS = "humidity_avg_hysteresis"
CONF_HUMIDITY_AVG_HYSTERESIS_DEVICES = "humidity_avg_hysteresis_devices"
CONF_TEMPERATURE_AVG_HYSTERESIS = "temperature_avg_hysteresis"
CONF_TEMPERATURE_AVG_HYSTERESIS_DEVICES = "temperature_avg_hysteresis_devices"
CONF_TEMPERATURE_AVG_WINDOW_MIN = "temperature_avg_window_min"
CONF_SLAVE_FILTER_SOFT_RESET = "slave_filter_soft_reset"
CONF_FILTER_ACK_TTL_DAYS = "filter_ack_ttl_days"

# NeuraCell-X: radon protection and dew-point control. The options of the
# bridge; where the bridge names an MQTT topic, a Home Assistant entity is named.
CONF_NEURACELL_ENABLED = "neuracell_enabled"
CONF_RADON_SOURCE = "radon_source"
CONF_RADON_ENTITIES = "radon_entities"
CONF_RADON_ALARM_ENTITY = "radon_alarm_entity"
CONF_RADON_THRESHOLD = "radon_threshold"
CONF_RADON_HYSTERESIS = "radon_hysteresis"
CONF_RADON_PROTECTION_FAN = "radon_protection_fan"
CONF_RADON_METER_TIMEOUT = "radon_meter_timeout"
CONF_RADON_DEVICE_SERIAL = "radon_device_serial"
CONF_RADON_DEVICE_ALARM_FIELD = "radon_device_alarm_field"
CONF_RADON_DEVICE_ALARM_VALUES = "radon_device_alarm_values"
CONF_DEWPOINT_ENABLED = "dewpoint_enabled"
CONF_DEWPOINT_SOURCE = "dewpoint_source"
CONF_DEWPOINT_BLOCK_ENTITY = "dewpoint_block_entity"
CONF_DEWPOINT_CONTROLLER_ENTITY = "dewpoint_controller_entity"
CONF_DEWPOINT_BLOCK_DEVICES = "dewpoint_block_devices"
CONF_DEWPOINT_DEVICE_SERIAL = "dewpoint_device_serial"
CONF_DEWPOINT_DEVICE_BLOCK_MODES = "dewpoint_device_block_modes"
CONF_DEWPOINT_INDOOR_TEMP = "dewpoint_indoor_temp_entity"
CONF_DEWPOINT_INDOOR_HUMIDITY = "dewpoint_indoor_humidity_entity"
CONF_DEWPOINT_OUTDOOR_TEMP = "dewpoint_outdoor_temp_entity"
CONF_DEWPOINT_OUTDOOR_HUMIDITY = "dewpoint_outdoor_humidity_entity"
CONF_DEWPOINT_MARGIN = "dewpoint_margin"
CONF_DEWPOINT_HYSTERESIS = "dewpoint_hysteresis"
CONF_DEWPOINT_SIGNAL_TIMEOUT = "dewpoint_signal_timeout"
CONF_DEWPOINT_CONTROLLER_TIMEOUT = "dewpoint_controller_timeout"
CONF_DEWPOINT_FALLBACK_TIMEOUT = "dewpoint_fallback_timeout"
CONF_DEWPOINT_FALLBACK_FAN = "dewpoint_fallback_fan"
CONF_DEWPOINT_LOST_ACTION = "dewpoint_lost_action"

RADON_OPTIONS = {
    CONF_NEURACELL_ENABLED: True,
    CONF_RADON_SOURCE: "signal",
    CONF_RADON_ENTITIES: [],
    CONF_RADON_ALARM_ENTITY: "",
    CONF_RADON_THRESHOLD: 300,
    CONF_RADON_HYSTERESIS: 50,
    CONF_RADON_PROTECTION_FAN: "low",
    CONF_RADON_METER_TIMEOUT: 30,
    CONF_RADON_DEVICE_SERIAL: "",
    CONF_RADON_DEVICE_ALARM_FIELD: "air_quality",
    CONF_RADON_DEVICE_ALARM_VALUES: "Bad,Poor,Very Bad,Alarm,Alert",
}
DEWPOINT_OPTIONS = {
    CONF_DEWPOINT_ENABLED: True,
    CONF_DEWPOINT_SOURCE: "signal",
    CONF_DEWPOINT_BLOCK_ENTITY: "",
    CONF_DEWPOINT_CONTROLLER_ENTITY: "",
    CONF_DEWPOINT_BLOCK_DEVICES: "",
    CONF_DEWPOINT_DEVICE_SERIAL: "",
    CONF_DEWPOINT_DEVICE_BLOCK_MODES: "Off",
    CONF_DEWPOINT_INDOOR_TEMP: "",
    CONF_DEWPOINT_INDOOR_HUMIDITY: "",
    CONF_DEWPOINT_OUTDOOR_TEMP: "",
    CONF_DEWPOINT_OUTDOOR_HUMIDITY: "",
    CONF_DEWPOINT_MARGIN: 1.0,
    CONF_DEWPOINT_HYSTERESIS: 0.5,
    CONF_DEWPOINT_SIGNAL_TIMEOUT: 0,
    CONF_DEWPOINT_CONTROLLER_TIMEOUT: 30,
    CONF_DEWPOINT_FALLBACK_TIMEOUT: 30,
    CONF_DEWPOINT_FALLBACK_FAN: "low",
    CONF_DEWPOINT_LOST_ACTION: "keep",
}

GENERAL_OPTIONS = {
    CONF_POLL_INTERVAL: UPDATE_INTERVAL,
    CONF_FAILURE_THRESHOLD: 3,
    CONF_COMMAND_COALESCE_MS: 800,
    CONF_AVERAGE_WINDOW_MIN: 30,
    CONF_AVERAGE_WINDOW_DEVICES: "",
    CONF_HUMIDITY_AVG_HYSTERESIS: 0.0,
    CONF_HUMIDITY_AVG_HYSTERESIS_DEVICES: "",
    CONF_TEMPERATURE_AVG_HYSTERESIS: 0.0,
    CONF_TEMPERATURE_AVG_HYSTERESIS_DEVICES: "",
    CONF_TEMPERATURE_AVG_WINDOW_MIN: 0,
    CONF_SLAVE_FILTER_SOFT_RESET: False,
    CONF_FILTER_ACK_TTL_DAYS: 90,
}
DEFAULT_OPTIONS = {**GENERAL_OPTIONS, **RADON_OPTIONS, **DEWPOINT_OPTIONS}

# Seconds after a filter reset before the status is read again to see whether
# the counter has changed (the cloud acknowledges the call either way).
FILTER_RESET_VERIFY_DELAY = 12.0
# A maintenance acknowledgement of a Slave ends when the unit itself reports
# "Good" for this many readings in a row and for at least this long.
FILTER_ACK_CLEAR_POLLS = 10
FILTER_ACK_CLEAR_MIN_S = 600.0
# The cloud answers a mode change as soon as it has accepted the call. A
# warning is written if the unit still reports another mode after this time.
MODE_VERIFY_WINDOW_S = 180.0
# A Slave is shown with the mode of its zone Master if the Master was read
# within this many seconds.
MASTER_MODE_MAX_AGE_S = 300.0

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
