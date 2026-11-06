"""Wall-panel idle behaviour by time of day, driven by pyscript/wall_panels.yaml.

lovelace-wallpanel owns idle on each tablet: its timer starts the screensaver, a
touch stops it, and it mirrors that into input_boolean.wall_idle_<panel>. This app
tells it which screensaver to use and sets brightness to match. Brightness, the
night window and Keep screen on are helpers on the Settings view
(packages/wall_idle.yaml), so a change there applies at once. Per panel it
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
BRIGHTNESS = "input_number.wall_brightness"
NIGHT_START = "input_datetime.wall_night_start"
NIGHT_END = "input_datetime.wall_night_end"
KEEP_ON = "input_boolean.wall_keep_screen_on"
# Set once the helpers hold the settings_seed values, so a restart keeps edits.
SEEDED = "pyscript.wall_settings_seeded"
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


def _is_night():
    now = dt_util.now().strftime("%H:%M")
    start, end = state.get(NIGHT_START)[:5], state.get(NIGHT_END)[:5]
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _too_dark_for_motion(panel):
    """A camera in a dark room reports its own noise as motion, so a dimmed panel
    would wake itself every cycle. A sensorless tablet reports a negative lux."""
    try:
        lux = float(state.get(panel["light"]))
    except ValueError:
        return False
    return 0 <= lux < panel["motion_min_lux"]


def _screensaver(panel, night):
    if state.get(KEEP_ON) == "on":
        return "off"
    if not night:
        return panel["day"]["screensaver"]
    return SLEEP_SCREENSAVERS[panel.get("sleep_behavior") or state.get(SLEEP_SELECT)]


def _apply(panel_id):
    panel = PANELS[panel_id]
    night = _is_night()
    state.set(f"sensor.wall_screensaver_{panel_id}", _screensaver(panel, night))
    idle = state.get(f"input_boolean.wall_idle_{panel_id}") == "on"
    period = panel["night"] if night else panel["day"]
    if idle and state.get(KEEP_ON) != "on":
        level = period["idle_brightness"]
    else:
        level = int(float(state.get(BRIGHTNESS)))
    if state.get(panel["brightness"]) != "unavailable":
        service.call("number", "set_value", entity_id=panel["brightness"], value=level)


def _watch(panel_id, panel):
    idle_entity = f"input_boolean.wall_idle_{panel_id}"

    @state_trigger(idle_entity)
    def idle_changed():
        _apply(panel_id)

    TRIGGERS.append(idle_changed)

    if panel["motion_wake"]:

        @state_trigger(f"{panel['motion']} == 'on'")
        def motion_seen():
            if not _too_dark_for_motion(panel):
                service.call("input_boolean", "turn_off", entity_id=idle_entity)

        TRIGGERS.append(motion_seen)


def _apply_all():
    for panel_id in PANELS:
        _apply(panel_id)


@state_trigger(SLEEP_SELECT, BRIGHTNESS, NIGHT_START, NIGHT_END, KEEP_ON)
def wall_setting_changed():
    _apply_all()


# Fired by the wall_panels_period_change automation at night start and end.
@event_trigger("wall_period_changed")
def wall_period_changed():
    _apply_all()


def _seed(seed):
    state.persist(SEEDED, default_value="no")
    if state.get(SEEDED) == "yes":
        return
    service.call("input_number", "set_value", entity_id=BRIGHTNESS, value=seed["brightness"], blocking=True)
    service.call("input_datetime", "set_datetime", entity_id=NIGHT_START, time=seed["night_start"], blocking=True)
    service.call("input_datetime", "set_datetime", entity_id=NIGHT_END, time=seed["night_end"], blocking=True)
    state.set(SEEDED, "yes")


@time_trigger("startup")
def wall_idle_startup():
    """Publish every panel's settings, then tell the panels they can load them."""
    # wallpanel's per-tablet settings need window.browser_mod, which only loads
    # once the integration has its single config entry.
    if not hass.config_entries.async_entries("browser_mod"):
        await hass.config_entries.flow.async_init("browser_mod", context={"source": "user"})

    data = yaml.safe_load(task.executor(pathlib.Path(DATA_PATH).read_text))
    _seed(data["settings_seed"])
    for panel_id, overrides in data["panels"].items():
        panel = _merge(data["defaults"], overrides)
        PANELS[panel_id] = panel
        state.set(f"sensor.wall_idle_seconds_{panel_id}", panel["idle_seconds"])
        _watch(panel_id, panel)
        _apply(panel_id)
        action = "turn_on" if panel["motion_wake"] else "turn_off"
        service.call("switch", action, entity_id=panel["motion_switch"])
    event.fire("wall_panels_ready")
