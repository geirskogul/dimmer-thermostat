# Dimmer Thermostat

Proportional (PI) temperature control of a heat source on a dimmer, as a Home
Assistant custom integration with a UI config flow.

Built for a reptile basking or infrared lamp on a dimmer module, but it
works for anything whose heat output is meaningfully variable rather than
on/off: a heat mat on a dimmable outlet, a dimmable tubular heater, a number
entity that sets a level from 0 to 100.

## Compatible dimmers

It works with **any dimmer Home Assistant can expose**, whatever the protocol:

- a `light` entity that supports brightness (Wi-Fi, Zigbee, Z-Wave, Matter/Thread, MQTT, and so on), or
- a `number` / `input_number` entity, scaled across its own min and max.

So Wi-Fi dimmers (Shelly, Tasmota, ESPHome, Kasa, Tuya and similar), Zigbee (ZHA or
Zigbee2MQTT), Z-Wave and Matter all work. The temperature sensor can be any
`sensor` with device class `temperature`, wired or wireless.

Notes for Wi-Fi dimmers:

- Prefer a **local** integration over a cloud one. Cloud round trips add latency,
  rate limits and a dependency on your internet connection.
- Set the dimmer's power-on behaviour to off or a low level, not "last state".
- If the device is slow or rate-limited, use the *send deadband* and *resend
  interval* settings to send fewer commands.
- The soft failsafe cannot help if the dimmer drops off Wi-Fi while holding a
  level, exactly as with any other wireless link. Keep the hardware cutoff (see Safety).

## Why not the built-in Generic Thermostat

Home Assistant's Generic Thermostat drives a **switch**: below target the heater
turns on, at target it turns off. That throws away the point of owning a dimmer.
Bang-bang control of a basking lamp gives visible on/off cycling, a saw-tooth
temperature, and thermal cycling that shortens bulb life.

This integration instead holds the lamp at whatever steady percentage balances
the enclosure's heat loss, and corrects around that.

There is deliberately **no derivative term**. Battery-powered wireless temperature sensors (Zigbee, Z-Wave, Bluetooth) quantise
to 0.1–0.5 °C and report every few minutes; a D term would differentiate a
staircase and amplify noise far more than it would damp anything.

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**
2. Add this repository's URL, category **Integration**
3. Install, then restart Home Assistant
4. **Settings → Devices & services → + Add integration → Dimmer Thermostat**

### Manual

Copy `custom_components/dimmer_thermostat/` into your `config/custom_components/`
directory and restart Home Assistant.

## Setup

The add-integration form asks for four things:

| Field | Notes |
| --- | --- |
| Temperature sensor | Any `sensor` with device class `temperature` |
| Dimmer | A dimmable `light`, or a `number` / `input_number` |
| Target temperature | Changeable later from the thermostat card |
| Maximum output | The controller never commands more than this |

You get a device with a `climate` entity (standard thermostat card, schedules,
voice, everything), plus three sensors: output %, the PI integral, and a
controller status string.

Tuning lives in **Configure** on the integration entry, grouped into
Regulation, Limits and Safety. Changing the sensor or the dimmer later is under
**Reconfigure**.

## Tuning

1. **Find the holding output by hand.** Turn the thermostat off. Set the dimmer
   to 20 %, wait 45 minutes, record the temperature. Repeat at 40 % and 60 %.
   You now know roughly what percentage holds your target, and what one percent
   is worth in degrees. Put the holding value in *Startup output*.
2. **Set the gain.** *Proportional gain* ≈ `10 / (degrees per percent)`, so a
   1 °C error moves the output by about a tenth of full range. Set *Integral
   time* very long (say 600 min) and confirm the loop settles somewhere stable
   but below setpoint — that steady offset is what proportional-only control
   does, and it is expected.
3. **Close the offset.** Shorten *Integral time* until the offset closes in
   20–40 minutes without overshoot. If it cycles slowly above and below
   setpoint, integral time is too short; double it.

A vivarium is a slow, heavily damped system. Err long on integral time. Fast is
not the goal; not overshooting onto a basking animal is.

The shipped defaults (gain 12 %/°C, integral time 30 min) were checked against a
first-order model of a small enclosure with a 100 W lamp: about 0.3 °C of
overshoot on a cold start, settling in roughly half an hour, and no steady-state
error after a 6 °C drop in ambient. Your enclosure is not that model — tune it.

## Safety

The integration's own guards are **soft** ones:

- Sensor unavailable, non-numeric, stale or implausible → output drops to the
  configured minimum, with a notification.
- Measured temperature above setpoint + margin → dimmer cut outright.
- Dimmer at maximum for a long stretch while still too cold → notification,
  because that usually means a dead bulb.
- Unloading, reloading or removing the integration parks the dimmer, so removing
  it can never leave a lamp stranded at a fixed brightness with nothing watching.

**None of that helps if the wireless link (Zigbee, Wi-Fi, Z-Wave, ...) drops while the
dimmer is holding a level.** The dimmer will happily sit at 70 % forever and Home Assistant is in no
position to notice. Fit an independent hardware over-temperature cutoff — a
mechanical thermostat or thermal cutoff inline with the lamp, set well above
your target.

If the animal matters to you, the honest recommendation is a dedicated dimming
reptile thermostat (Herpstat, Vivarium Electronics, Habistat) as the actual
controller, with Home Assistant doing scheduling, logging and alerting on top.
This integration is good, but it is a hobby-grade loop over a consumer radio
protocol.

## Things that will actually bite you

**Sensor reporting interval.** The big one. Most battery-powered wireless temperature sensors (Zigbee, Z-Wave, BLE)
default to reporting only on a 0.5 °C change with a 30–60 minute heartbeat. A
control loop cannot work on that. For Zigbee: in Zigbee2MQTT set the temperature cluster
reporting to roughly min 30 s / max 300 s / change 10 (0.1 °C); in ZHA use
`zha-toolkit` or the cluster config page. Keep *Sensor staleness timeout*
comfortably longer than that max interval, or the failsafe will nuisance-trip
when the temperature is genuinely steady.

**Where the probe is.** An air sensor near the lamp measures air, not the
basking surface, which under an IR bulb can be 15–20 °C hotter than air 10 cm
away. Verify the real surface temperature with an IR gun at several dimmer
levels, and set *Maximum output* so that even flat out the surface stays safe.

**The dimmer's load rating.** An IR bulb is a resistive incandescent-family
load, so a phase-cut dimmer handles it — this is how commercial dimming reptile
thermostats work. Check the module is rated for the wattage with a *resistive*
load rather than "LED only", and set its power-on behaviour to off or a low
level, not "last state". Do not put a UVB tube or anything LED on this circuit.

## Services

`dimmer_thermostat.reset_integral` clears the accumulated integral term,
optionally preloading it to a percentage. Use it after changing the bulb, the
fixture or the enclosure.

## Requirements

Home Assistant 2025.2 or newer.

## Licence

MIT
