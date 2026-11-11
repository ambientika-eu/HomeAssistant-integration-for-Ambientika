"""Compatibility layer between the Ambientika cloud API and ambientika_py.

ambientika_py resolves the values of a status packet with plain ``Enum[...]``
lookups. The cloud API, however, knows more values than the pinned library:
the API schema lists the fan speeds Low, Medium, High, Night and Turbo. A unit
reports ``Night`` while it runs its quiet night step and ``Turbo`` while it runs
its boost step - both chosen by the unit itself (for example in Smart mode), so
a status read can return them at any time.

This module registers the known additional values and makes the lookups
tolerant, so that a value unknown to the library is registered at runtime
instead of interrupting the status read. Such values are read-only: they are
shown as state, but never sent back to the API.
"""

from __future__ import annotations

from ambientika_py import FanSpeed, HumidityLevel, OperatingMode

from .const import LOGGER

COMPAT_ENUM_MEMBERS: dict = {}  # enum class -> set of names added at runtime
_COMPAT_AUTO_BASE = 900  # value range for auto-registered members

KNOWN_MISSING_ENUM_MEMBERS = {
    "FanSpeed": (("Night", 3), ("Turbo", 4)),
}


def _register_enum_member(enum_cls, name: str, value: int):
    """Add a member to an existing IntEnum at runtime."""
    member = int.__new__(enum_cls, value)
    member._name_ = name
    member._value_ = value
    type.__setattr__(enum_cls, name, member)
    dict.__setitem__(enum_cls._member_map_, name, member)
    enum_cls._value2member_map_.setdefault(value, member)
    if name not in enum_cls._member_names_:
        enum_cls._member_names_.append(name)
    COMPAT_ENUM_MEMBERS.setdefault(enum_cls, set()).add(name)
    return member


class _TolerantMemberMap(dict):
    """Member map that registers unknown names instead of raising KeyError."""

    def __init__(self, enum_cls, data) -> None:
        super().__init__(data)
        self._enum_cls = enum_cls

    def __missing__(self, key):
        cls = self._enum_cls
        LOGGER.warning(
            "Ambientika API reported the %s value %r, registering it as a "
            "read-only value",
            cls.__name__,
            key,
        )
        # The API may also send an undefined value as a bare number (or null).
        # An enum member needs a string name, so register it under str(key) and
        # map the raw key to the same member.
        name = key if isinstance(key, str) else str(key)
        member = dict.get(self, name)
        if member is None:
            value = _COMPAT_AUTO_BASE + len(COMPAT_ENUM_MEMBERS.get(cls, ()))
            member = _register_enum_member(cls, name, value)
        if not isinstance(key, str):
            dict.__setitem__(self, key, member)
        return member


def is_compat_member(enum_cls, value) -> bool:
    """Return True for a read-only value (Night, Turbo, auto-registered)."""
    name = getattr(value, "name", None)
    return name is not None and name in COMPAT_ENUM_MEMBERS.get(enum_cls, ())


def writable_members(enum_cls) -> list:
    """Return the members that may be sent to the API, in definition order."""
    return [member for member in enum_cls if not is_compat_member(enum_cls, member)]


def strict_enum_lookup(enum_cls, name):
    """Look up a writable member WITHOUT the tolerant fallback (command path)."""
    member = dict.get(enum_cls._member_map_, name)
    if member is None or is_compat_member(enum_cls, member):
        return None
    return member


def install_enum_compat() -> None:
    """Register the known additional values and make the lookups tolerant."""
    for enum_cls in (OperatingMode, FanSpeed, HumidityLevel):
        for name, value in KNOWN_MISSING_ENUM_MEMBERS.get(enum_cls.__name__, ()):
            if name not in enum_cls._member_map_:
                _register_enum_member(enum_cls, name, value)
        if not isinstance(enum_cls._member_map_, _TolerantMemberMap):
            type.__setattr__(
                enum_cls,
                "_member_map_",
                _TolerantMemberMap(enum_cls, enum_cls._member_map_),
            )


install_enum_compat()
