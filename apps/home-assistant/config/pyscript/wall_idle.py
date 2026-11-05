"""Wall-panel idle behaviour by time of day, driven by pyscript/wall_panels.yaml.

lovelace-wallpanel owns idle on each tablet: its timer starts the screensaver, a
touch stops it, and it mirrors that into input_boolean.wall_idle_<panel>. This app
tells it which screensaver to use and sets brightness to match. Per panel it
publishes:
    sensor.wall_screensaver_<panel>    wallpanel profile_entity
    sensor.wall_idle_seconds_<panel>   wallpanel idle_time, read at page load
Why this shape: docs/wall-panels.md "Idle behaviour".
"""

import pathlib

import homeassistant.util.dt as dt_util
import yaml

DATA_PATH = "/config/pyscript/wall_panels.yaml"
SLEEP_SELECT = "input_select.wall_sleep_behavior"
# Sleep behavior option -> screensaver. Display off is not offered: no tablet can
# power its panel off, see docs/wall-panels.md.
SLEEP_SCREENSAVERS = {"Dim view": "off", "Black screensaver": "black"}

PANELS = {}
# pyscript drops a trigger closure that nothing references.
TRIGGERS = []


def _merge(base, over):
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _is_night(window):
    now = dt_util.now().strftime("%H:%M")
    start, end = window["start"], window["end"]
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _screensaver(panel, night):
    if not night:
        return panel["day"]["screensaver"]
    return SLEEP_SCREENSAVERS[panel.get("sleep_behavior") or state.get(SLEEP_SELECT)]


def _apply(panel_id):
    panel = PANELS[panel_id]
    night = _is_night(panel["night_window"])
    state.set(f"sensor.wall_screensaver_{panel_id}", _screensaver(panel, night))
    idle = state.get(f"input_boolean.wall_idle_{panel_id}") == "on"
    period = panel["night"] if night else panel["day"]
    level = period["idle_brightness"] if idle else panel["active_brightness"]
    if state.get(panel["brightness"]) != "unavailable":
        service.call("number", "set_value", entity_id=panel["brightness"], value=level)


def _cron(hhmm):
    hour, minute = hhmm.split(":")
    return f"cron({int(minute)} {int(hour)} * * *)"


def _watch(panel_id, panel):
    idle_entity = f"input_boolean.wall_idle_{panel_id}"
    window = panel["night_window"]

    @state_trigger(idle_entity)
    def idle_changed():
        _apply(panel_id)

    @time_trigger(_cron(window["start"]), _cron(window["end"]))
    def period_changed():
        _apply(panel_id)

    TRIGGERS.extend([idle_changed, period_changed])

    if panel["motion_wake"]:

        @state_trigger(f"{panel['motion']} == 'on'")
        def motion_seen():
            service.call("input_boolean", "turn_off", entity_id=idle_entity)

        TRIGGERS.append(motion_seen)


@state_trigger(SLEEP_SELECT)
def wall_sleep_behavior_changed():
    for panel_id in PANELS:
        _apply(panel_id)


@time_trigger("startup")
def wall_idle_startup():
    """Publish every panel's settings, then tell the panels they can load them."""
    # wallpanel's per-tablet settings need window.browser_mod, which only loads
    # once the integration has its single config entry.
    if not hass.config_entries.async_entries("browser_mod"):
        await hass.config_entries.flow.async_init("browser_mod", context={"source": "user"})

    data = yaml.safe_load(task.executor(pathlib.Path(DATA_PATH).read_text))
    for panel_id, overrides in data["panels"].items():
        panel = _merge(data["defaults"], overrides)
        PANELS[panel_id] = panel
        state.set(f"sensor.wall_idle_seconds_{panel_id}", panel["idle_seconds"])
        _watch(panel_id, panel)
        _apply(panel_id)
        action = "turn_on" if panel["motion_wake"] else "turn_off"
        service.call("switch", action, entity_id=panel["motion_switch"])
    event.fire("wall_panels_ready")
