"""The PI control loop behind the Dimmer Thermostat climate entity.

Control strategy
----------------
A basking / infrared lamp on a dimmer is a proportional actuator, so this uses a
PI controller rather than the on/off (bang-bang) control of HA's built-in Generic
Thermostat. There is deliberately no derivative term: wireless temperature sensors
quantise to 0.1-0.5 degrees and report every few minutes, so a D term would
amplify quantisation noise far more than it would damp anything.

Safety
------
Every path through `async_control` either commands the dimmer or fails safe. The
failsafe level is the configured minimum output (0 by default), because for a
live animal an enclosure that is too cold is survivable for far longer than one
that is too hot. This is still only a soft failsafe: it cannot help if the
wireless link (Zigbee, Wi-Fi, ...) drops while the dimmer is holding a level. Keep an independent hardware
over-temperature cutoff in the circuit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import NamedTuple

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.light import ATTR_BRIGHTNESS_PCT
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_INTEGRAL,
    CONF_BACKUP_SENSOR,
    CONF_CYCLE_SECONDS,
    CONF_DIMMER,
    CONF_KP,
    CONF_MAX_OUTPUT,
    CONF_MIN_OUTPUT,
    CONF_OVERTEMP_MARGIN,
    CONF_RESEND_SECONDS,
    CONF_SATURATION_ALERT,
    CONF_SEND_DEADBAND,
    CONF_SENSOR,
    CONF_SENSOR_MAX_AGE,
    CONF_STARTUP_OUTPUT,
    CONF_TARGET_TEMP,
    CONF_TEMP_MAX_VALID,
    CONF_TEMP_MIN_VALID,
    CONF_TI_MINUTES,
    DEFAULTS,
    LIGHT_DOMAIN,
    NOTIFY_THROTTLE_SECONDS,
    NUMBER_DOMAINS,
    STATUS_DEGRADED,
    STATUS_FAILSAFE,
    STATUS_OFF,
    STATUS_OK,
    STATUS_OVERTEMP,
    STATUS_STARTING,
)

_LOGGER = logging.getLogger(__name__)

_INVALID_STATES = (None, "", STATE_UNKNOWN, STATE_UNAVAILABLE)


def _clamp(value: float, low: float, high: float) -> float:
    """Constrain value to the inclusive range [low, high]."""
    return max(low, min(high, value))


class _Reading(NamedTuple):
    """One sensor's reading, or the reason it cannot be trusted."""

    value: float | None
    problem: str | None = None
    too_hot: bool = False


class DimmerThermostatController:
    """Owns the control loop, the actuator and the published controller state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set the controller up without starting it or touching the actuator."""
        self.hass = hass
        self.entry = entry

        self.hvac_mode: HVACMode = HVACMode.OFF
        self.target_temperature: float = self._opt(CONF_TARGET_TEMP)
        self.current_temperature: float | None = None
        self.temperature_source: str | None = None
        self.output: float = 0.0
        self.integral: float = self._opt(CONF_STARTUP_OUTPUT)
        self.status: str = STATUS_STARTING
        self.status_detail: str = "not started"

        self._listeners: list[Callable[[], None]] = []
        self._unsubscribers: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._started = False

        self._last_step_monotonic: float | None = None
        self._last_send_monotonic: float | None = None
        self._last_sent_output: float | None = None
        self._saturated_since: float | None = None
        self._last_notify_monotonic: dict[str, float] = {}
        self._sensor_problems: list[str] = []

    # -- configuration access -------------------------------------------------

    def _opt(self, key: str) -> float:
        """Read a tunable, preferring options over entry data over the default."""
        if key in self.entry.options:
            return float(self.entry.options[key])
        if key in self.entry.data:
            return float(self.entry.data[key])
        return float(DEFAULTS[key])

    @property
    def sensor_entity_ids(self) -> list[str]:
        """The primary temperature sensor, then the backup if one is set."""
        sensors = [self.entry.data[CONF_SENSOR]]
        backup = self.entry.data.get(CONF_BACKUP_SENSOR)
        if backup and backup not in sensors:
            sensors.append(backup)
        return sensors

    @property
    def dimmer_entity_id(self) -> str:
        """Entity id of the dimmer being driven."""
        return self.entry.data[CONF_DIMMER]

    @property
    def min_output(self) -> float:
        """Lowest percentage the controller will command."""
        return self._opt(CONF_MIN_OUTPUT)

    @property
    def max_output(self) -> float:
        """Highest percentage the controller will command."""
        return self._opt(CONF_MAX_OUTPUT)

    @property
    def hvac_action(self) -> HVACAction:
        """What the thermostat is doing right now, for the UI."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING if self.output > 0 else HVACAction.IDLE

    # -- listener plumbing ----------------------------------------------------

    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity callback and return the function that removes it."""
        self._listeners.append(update_callback)

        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    def _async_notify_listeners(self) -> None:
        """Push the current controller state out to every subscribed entity."""
        for update_callback in list(self._listeners):
            update_callback()

    # -- lifecycle ------------------------------------------------------------

    async def async_start(self) -> None:
        """Begin controlling: subscribe to the sensor, start the tick, run once."""
        if self._started:
            return
        self._started = True

        cycle = timedelta(seconds=int(self._opt(CONF_CYCLE_SECONDS)))
        self._unsubscribers.append(
            async_track_time_interval(self.hass, self._async_tick, cycle)
        )
        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, self.sensor_entity_ids, self._async_sensor_event
            )
        )
        await self.async_control()

    async def async_stop(self) -> None:
        """Stop the loop and park the dimmer at the failsafe level.

        Unload happens on reload, reconfiguration and removal alike. Parking the
        lamp costs a brief dip in temperature on a reload, which a thermal system
        will not notice, and it means removing the integration can never leave a
        heat lamp stranded at a fixed brightness with nothing watching it.
        """
        self._started = False
        while self._unsubscribers:
            self._unsubscribers.pop()()
        await self._async_drive_dimmer(self.min_output, force=True)

    # -- triggers -------------------------------------------------------------

    async def _async_tick(self, now: datetime) -> None:
        """Periodic safety tick; runs even when the sensor has gone quiet."""
        await self.async_control()

    async def _async_sensor_event(self, event: Event[EventStateChangedData]) -> None:
        """React immediately to a fresh report from either sensor."""
        await self.async_control()

    # -- commands from the climate entity -------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch between heating and off, acting on the dimmer straight away."""
        self.hvac_mode = hvac_mode
        if hvac_mode == HVACMode.OFF:
            self.integral = self._opt(CONF_STARTUP_OUTPUT)
            await self._async_shutdown("turned off")
            return
        await self.async_control()

    async def async_set_target_temperature(self, temperature: float) -> None:
        """Accept a new setpoint and re-run the loop immediately."""
        self.target_temperature = float(temperature)
        await self.async_control()

    async def async_reset_integral(self, preload: float | None = None) -> None:
        """Clear the accumulated integral term, optionally preloading it."""
        default = self._opt(CONF_STARTUP_OUTPUT)
        self.integral = default if preload is None else float(preload)
        await self.async_control()

    def async_restore(self, last_state: State | None) -> None:
        """Adopt the mode, setpoint and integral recorded before a restart."""
        if last_state is None:
            return
        _restore_mode(self, last_state)
        _restore_target(self, last_state)
        _restore_integral(self, last_state)

    # -- the control step -----------------------------------------------------

    async def async_control(self) -> None:
        """Run one full control pass. Serialised so triggers cannot overlap."""
        async with self._lock:
            await self._async_control_locked()
        self._async_notify_listeners()

    async def _async_control_locked(self) -> None:
        """Body of the control pass; the caller holds the lock."""
        if self.hvac_mode == HVACMode.OFF:
            self._read_temperature_quietly()
            await self._async_shutdown("thermostat off")
            return

        temperature = await self._async_validated_temperature()
        if temperature is None:
            return

        self.current_temperature = temperature
        await self._async_regulate(temperature)

    def _read_temperature_quietly(self) -> None:
        """Track the sensors while switched off, without guards or failsafes.

        A thermostat that is off should still show the room temperature, and
        should still be available so the user can switch it back on.
        """
        readings: dict[str, float] = {}
        for entity_id in self.sensor_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in _INVALID_STATES:
                continue
            try:
                readings[entity_id] = float(state.state)
            except (TypeError, ValueError):
                continue
        if readings:
            self.temperature_source = max(readings, key=readings.__getitem__)
            self.current_temperature = readings[self.temperature_source]

    async def _async_regulate(self, temperature: float) -> None:
        """Normal operation: over-temperature check, then one PI step."""
        margin = self._opt(CONF_OVERTEMP_MARGIN)
        if temperature >= self.target_temperature + margin:
            await self._async_handle_overtemp(temperature)
            return

        output = self._compute_output(temperature, self._step_interval())
        await self._async_drive_dimmer(output)
        self._track_saturation(output, self.target_temperature - temperature)
        detail = (
            f"temp={temperature:.2f} ({self.temperature_source}) "
            f"target={self.target_temperature:.2f} "
            f"out={output:.1f}% i={self.integral:.1f}%"
        )
        if self._sensor_problems:
            self._set_status(
                STATUS_DEGRADED, f"{detail}; {'; '.join(self._sensor_problems)}"
            )
            return
        self._set_status(STATUS_OK, detail)

    def _compute_output(self, temperature: float, dt_seconds: float) -> float:
        """Advance the integral and return the commanded output percentage.

        Anti-windup is by conditional integration: the integral is frozen while
        the actuator is already against a stop and the error would only push it
        further in. Merely clamping the integral to the actuator range is not
        enough, because during a cold start the proportional term alone pins the
        output at maximum, and an integral that keeps accumulating through that
        period has to be unwound afterwards -- which is overshoot, paid for on
        top of the animal.
        """
        error = self.target_temperature - temperature
        kp = self._opt(CONF_KP)
        proportional = kp * error

        if not self._is_winding_up(proportional + self.integral, error):
            ti_seconds = max(self._opt(CONF_TI_MINUTES), 0.1) * 60.0
            self.integral = _clamp(
                self.integral + (kp / ti_seconds) * error * dt_seconds,
                self.min_output,
                self.max_output,
            )

        self.output = _clamp(proportional + self.integral, self.min_output, self.max_output)
        return self.output

    def _is_winding_up(self, unclamped_output: float, error: float) -> bool:
        """True when integrating further would only drive deeper into a stop.

        Both conditions release on their own as the error shrinks, so the
        integral can never be frozen permanently.
        """
        if unclamped_output >= self.max_output and error > 0:
            return True
        return unclamped_output <= self.min_output and error < 0

    def _step_interval(self) -> float:
        """Seconds since the previous step, clamped against clock oddities."""
        cycle = self._opt(CONF_CYCLE_SECONDS)
        now = time.monotonic()
        if self._last_step_monotonic is None:
            interval = cycle
        else:
            interval = _clamp(now - self._last_step_monotonic, 1.0, 5.0 * cycle)
        self._last_step_monotonic = now
        return interval

    # -- validation and failure paths -----------------------------------------

    async def _async_validated_temperature(self) -> float | None:
        """Return the hottest trustworthy reading, or None having failed safe.

        With a backup sensor the loop controls on whichever valid sensor reads
        higher, so either one can trip the over-temperature cut. A sensor that is
        unavailable, stale or implausibly low is dropped with a warning while
        the other carries on. A reading above the plausible range is never
        dropped: it may be a real overheat rather than a broken probe, so it
        fails safe exactly as a lone sensor would.
        """
        readings: dict[str, float] = {}
        problems: list[str] = []
        for entity_id in self.sensor_entity_ids:
            reading = self._read_sensor(entity_id)
            if reading.too_hot:
                await self._async_fail_safe(reading.problem)
                return None
            if reading.value is None:
                problems.append(reading.problem)
            else:
                readings[entity_id] = reading.value

        self._sensor_problems = problems if readings else []
        if not readings:
            await self._async_fail_safe("; ".join(problems))
            return None

        self.temperature_source = max(readings, key=readings.__getitem__)
        if problems:
            reason = "; ".join(problems)
            self._notify(
                "sensor",
                f"Running on {self.temperature_source} alone -- {reason}",
            )
            _LOGGER.warning("%s: sensor degraded, %s", self.entry.title, reason)
        return readings[self.temperature_source]

    def _read_sensor(self, entity_id: str) -> _Reading:
        """Apply the availability, staleness and plausibility guards to one sensor."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _INVALID_STATES:
            return _Reading(None, f"{entity_id} is unavailable")

        try:
            temperature = float(state.state)
        except (TypeError, ValueError):
            return _Reading(
                None, f"{entity_id} reported the non-numeric value {state.state!r}"
            )

        age = _state_age_seconds(state)
        max_age = self._opt(CONF_SENSOR_MAX_AGE)
        if age is not None and age > max_age:
            return _Reading(
                None,
                f"{entity_id} last reported {int(age)} s ago, "
                f"over the {int(max_age)} s limit",
            )

        low = self._opt(CONF_TEMP_MIN_VALID)
        high = self._opt(CONF_TEMP_MAX_VALID)
        if not low <= temperature <= high:
            return _Reading(
                None,
                f"{entity_id} reads {temperature}, outside the "
                f"plausible range {low} to {high}",
                too_hot=temperature > high,
            )

        return _Reading(temperature)

    async def _async_fail_safe(self, reason: str) -> None:
        """Park the dimmer, reset the integral and raise a notification."""
        await self._async_drive_dimmer(self.min_output, force=True)
        self.integral = self._opt(CONF_STARTUP_OUTPUT)
        self._set_status(STATUS_FAILSAFE, reason)
        self._notify("failsafe", f"Heating dropped to {self.min_output:.0f}% -- {reason}")
        _LOGGER.warning("%s: failsafe, %s", self.entry.title, reason)

    async def _async_shutdown(self, reason: str) -> None:
        """Quiet stop requested by the user; no notification is warranted."""
        await self._async_drive_dimmer(self.min_output, force=True)
        self._set_status(STATUS_OFF, reason)

    async def _async_handle_overtemp(self, temperature: float) -> None:
        """Hard cut above the over-temperature margin, bypassing the deadband."""
        self.integral = self.min_output
        await self._async_drive_dimmer(self.min_output, force=True)
        self._set_status(
            STATUS_OVERTEMP,
            f"temp={temperature:.2f} ({self.temperature_source}) "
            f"target={self.target_temperature:.2f}",
        )
        self._notify(
            "overtemp",
            f"Over temperature: {self.temperature_source} reads {temperature:.1f} "
            f"against a setpoint of {self.target_temperature:.1f}. "
            "The dimmer has been cut.",
        )

    def _track_saturation(self, output: float, error: float) -> None:
        """Warn when the lamp is flat out and still losing: likely a dead bulb."""
        if output < self.max_output - 0.5 or error < 1.0:
            self._saturated_since = None
            return

        now = time.monotonic()
        if self._saturated_since is None:
            self._saturated_since = now
            return

        elapsed = now - self._saturated_since
        if elapsed < self._opt(CONF_SATURATION_ALERT):
            return

        self._notify(
            "saturation",
            f"The dimmer has been at {self.max_output:.0f}% for "
            f"{int(elapsed / 60)} minutes and the enclosure is still {error:.1f} "
            "below setpoint. Check the bulb, the fixture and the enclosure."
        )

    # -- actuation ------------------------------------------------------------

    async def _async_drive_dimmer(self, output: float, force: bool = False) -> None:
        """Command the dimmer, subject to the send deadband unless forced."""
        output = _clamp(output, self.min_output, self.max_output)
        self.output = output
        if not force and not self._needs_send(output):
            return

        self._last_send_monotonic = time.monotonic()
        self._last_sent_output = output
        await self._async_call_actuator(output)

    def _needs_send(self, output: float) -> bool:
        """True when the level moved enough, or the keep-alive resend is due."""
        if self._last_sent_output is None or self._last_send_monotonic is None:
            return True
        if abs(output - self._last_sent_output) >= self._opt(CONF_SEND_DEADBAND):
            return True
        elapsed = time.monotonic() - self._last_send_monotonic
        return elapsed >= self._opt(CONF_RESEND_SECONDS)

    async def _async_call_actuator(self, output: float) -> None:
        """Dispatch to the right service call for the dimmer's domain."""
        entity_id = self.dimmer_entity_id
        domain = entity_id.split(".", 1)[0]

        if domain == LIGHT_DOMAIN:
            await self._async_call_light(entity_id, output)
        elif domain in NUMBER_DOMAINS:
            await self._async_call_number(domain, entity_id, output)
        else:
            _LOGGER.error("%s: %s is not a supported dimmer", self.entry.title, entity_id)

    async def _async_call_light(self, entity_id: str, output: float) -> None:
        """Drive a light entity with brightness_pct, turning it off at zero."""
        if output <= 0.5:
            await self.hass.services.async_call(
                LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
            )
            return
        await self.hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id, ATTR_BRIGHTNESS_PCT: int(round(output))},
            blocking=True,
        )

    async def _async_call_number(self, domain: str, entity_id: str, output: float) -> None:
        """Drive a number entity, scaling the percentage across its own range."""
        state = self.hass.states.get(entity_id)
        low = float(state.attributes.get("min", 0)) if state else 0.0
        high = float(state.attributes.get("max", 100)) if state else 100.0
        value = low + (high - low) * (output / 100.0)
        await self.hass.services.async_call(
            domain,
            "set_value",
            {ATTR_ENTITY_ID: entity_id, "value": round(value, 2)},
            blocking=True,
        )

    # -- status and notifications ---------------------------------------------

    def _set_status(self, status: str, detail: str) -> None:
        """Record the one-line controller status shown by the status sensor."""
        self.status = status
        self.status_detail = detail[:255]

    def _notify(self, kind: str, message: str) -> None:
        """Raise a persistent notification, throttled per entry and per kind.

        Throttling per kind means a nagging warning, such as a backup sensor
        that has dropped out, can never hold back an over-temperature alert.
        """
        now = time.monotonic()
        last = self._last_notify_monotonic.get(kind)
        if last is not None and now - last < NOTIFY_THROTTLE_SECONDS:
            return
        self._last_notify_monotonic[kind] = now
        async_create_notification(
            self.hass,
            message,
            title=f"{self.entry.title}",
            notification_id=f"dimmer_thermostat_{self.entry.entry_id}_{kind}",
        )


# -- restore helpers, kept out of the class to keep async_restore flat ---------


def _restore_mode(controller: DimmerThermostatController, last_state: State) -> None:
    """Restore the HVAC mode recorded before the restart."""
    if last_state.state in (HVACMode.HEAT, HVACMode.OFF):
        controller.hvac_mode = HVACMode(last_state.state)


def _restore_target(controller: DimmerThermostatController, last_state: State) -> None:
    """Restore the setpoint recorded before the restart."""
    stored = last_state.attributes.get("temperature")
    if stored is None:
        return
    try:
        controller.target_temperature = float(stored)
    except (TypeError, ValueError):
        _LOGGER.debug("Ignoring unreadable restored setpoint %r", stored)


def _restore_integral(controller: DimmerThermostatController, last_state: State) -> None:
    """Restore the integral term so a restart does not undo hours of settling."""
    stored = last_state.attributes.get(ATTR_INTEGRAL)
    if stored is None:
        return
    try:
        controller.integral = _clamp(
            float(stored), controller.min_output, controller.max_output
        )
    except (TypeError, ValueError):
        _LOGGER.debug("Ignoring unreadable restored integral %r", stored)


def _state_age_seconds(state: State) -> float | None:
    """Seconds since the state last published, or None if that is unknowable."""
    stamp = getattr(state, "last_reported", None) or state.last_updated
    if not isinstance(stamp, datetime):
        return None
    return (dt_util.utcnow() - stamp).total_seconds()
