"""Wall-panel plug settings — registry-write services any (non-admin) kiosk user
can call from the Settings view.

Home Assistant has no built-in "set area / set name" service, and its native
registry editors are admin-only and hidden in kiosk mode. This pyscript app is
the mechanism the wall Settings view uses to let ANY user configure a plug. A
pyscript service runs in HA's own context, so it can write the registries even
when the caller is a non-admin kiosk session — the deliberate, LAN/Tailscale-only
privileged surface noted in docs/wall-panels.md.

All metadata lives in HA's native registries (the "db"):
    room      -> core.area_registry + entity.area_id
    name      -> core.entity_registry name override
    icon      -> core.entity_registry icon override
    category  -> core.label_registry + entity.labels
    is-light  -> switch_as_x config entry (add / remove)

Flow: the Settings view taps a plug -> script.plug_edit_open calls
`pyscript.plug_load` (fills the staging input_* helpers from the registry) and
opens a browser_mod popup; Save calls `pyscript.plug_apply` (writes the staging
helpers back to the registries).
"""

import homeassistant.helpers.entity_registry as er
import homeassistant.helpers.area_registry as ar
import homeassistant.helpers.label_registry as lr

# Staging helpers (defined in packages/plug_settings.yaml). A FIXED set, so the
# editor auto-scales to any number of plugs — only the target pointer changes.
TARGET = "input_text.plug_edit_target"
NAME = "input_text.plug_edit_name"
ICON = "input_text.plug_edit_icon"
ROOM = "input_select.plug_edit_room"
NEW_ROOM = "input_text.plug_edit_new_room"
CATEGORY = "input_select.plug_edit_category"
IS_LIGHT = "input_boolean.plug_edit_is_light"

UNASSIGNED = "Unassigned"
NO_CATEGORY = "—"
# Baseline appliance categories, materialised as HA labels on startup so the
# category dropdown has options out of the box.
CATEGORIES = ["Lamp", "Appliance", "Fan", "Heater", "Printer"]


def _area_names():
    # NB: pyscript does not support generator expressions — use list comps.
    reg = ar.async_get(hass)
    return sorted([a.name for a in reg.async_list_areas()])


@time_trigger("startup")
def plug_settings_reconcile():
    """Ensure the baseline category labels exist (idempotent, non-destructive)."""
    reg = lr.async_get(hass)
    have = {lab.name for lab in reg.async_list_labels()}
    created = 0
    for name in CATEGORIES:
        if name not in have:
            reg.async_create(name=name)
            created += 1
    log.info(f"plug_settings: reconcile done (+{created} category labels)")


@service
def plug_load(entity_id=None):
    """Populate the staging helpers from a plug's current registry metadata.

    entity_id: the plug switch entity being edited (e.g.
        switch.athom_smart_plug_v3_613888_switch).
    """
    if not entity_id:
        log.warning("plug_load: called without entity_id")
        return

    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    label_reg = lr.async_get(hass)
    entry = ent_reg.async_get(entity_id)

    cur_name = entry.name if entry and entry.name else ""
    cur_icon = entry.icon if entry and entry.icon else ""
    cur_area = ""
    if entry and entry.area_id:
        area = area_reg.async_get_area(entry.area_id)
        cur_area = area.name if area else ""
    cur_cat = NO_CATEGORY
    if entry and entry.labels:
        for lid in entry.labels:
            lab = label_reg.async_get_label(lid)
            if lab and lab.name in CATEGORIES:
                cur_cat = lab.name
                break
    is_light = False
    for e in hass.config_entries.async_entries("switch_as_x"):
        if e.options.get("entity_id") == entity_id:
            is_light = True
            break

    # Refresh the dropdown option lists (rooms are dynamic) BEFORE selecting.
    rooms = [UNASSIGNED] + _area_names()
    cats = [NO_CATEGORY] + CATEGORIES
    service.call("input_select", "set_options", entity_id=ROOM, options=rooms)
    service.call("input_select", "set_options", entity_id=CATEGORY, options=cats)

    service.call("input_text", "set_value", entity_id=TARGET, value=entity_id)
    service.call("input_text", "set_value", entity_id=NAME, value=cur_name)
    service.call("input_text", "set_value", entity_id=ICON, value=cur_icon)
    service.call("input_text", "set_value", entity_id=NEW_ROOM, value="")
    service.call(
        "input_select", "select_option", entity_id=ROOM,
        option=cur_area if cur_area in rooms else UNASSIGNED,
    )
    service.call("input_select", "select_option", entity_id=CATEGORY, option=cur_cat)
    service.call(
        "input_boolean", "turn_on" if is_light else "turn_off", entity_id=IS_LIGHT,
    )
    log.info(
        f"plug_load: {entity_id} name={cur_name!r} area={cur_area!r} "
        f"category={cur_cat} is_light={is_light}"
    )


@service
def plug_apply():
    """Write the staging helpers back to the registries for the target plug."""
    entity_id = state.get(TARGET)
    if not entity_id:
        log.warning("plug_apply: no target set")
        return

    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    label_reg = lr.async_get(hass)
    if ent_reg.async_get(entity_id) is None:
        log.warning(f"plug_apply: {entity_id} not in entity registry")
        return

    name = (state.get(NAME) or "").strip()
    icon = (state.get(ICON) or "").strip()
    new_room = (state.get(NEW_ROOM) or "").strip()
    room = new_room or state.get(ROOM) or UNASSIGNED
    category = state.get(CATEGORY) or NO_CATEGORY
    want_light = state.get(IS_LIGHT) == "on"

    # room -> area_id (create the area if the user typed a new one)
    if room in ("", UNASSIGNED):
        area_id = None
    else:
        area = area_reg.async_get_area_by_name(room) or area_reg.async_create(room)
        area_id = area.id

    # category -> labels (create the label if missing)
    if category in ("", NO_CATEGORY):
        labels = set()
    else:
        lab = label_reg.async_get_label_by_name(category) or label_reg.async_create(
            name=category
        )
        labels = {lab.label_id}

    ent_reg.async_update_entity(
        entity_id,
        area_id=area_id,
        name=(name or None),
        icon=(icon or None),
        labels=labels,
    )

    _apply_is_light(entity_id, want_light)
    log.info(
        f"plug_apply: {entity_id} room={room!r} name={name!r} icon={icon!r} "
        f"category={category} is_light={want_light}"
    )


def _apply_is_light(entity_id, want):
    """Add or remove the switch_as_x (target_domain=light) config entry so the
    plug is / isn't controlled by 'turn off the lights'."""
    existing = [
        e for e in hass.config_entries.async_entries("switch_as_x")
        if e.options.get("entity_id") == entity_id
    ]
    if want and not existing:
        flow = await hass.config_entries.flow.async_init(
            "switch_as_x", context={"source": "user"}
        )
        await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {"entity_id": entity_id, "invert": False, "target_domain": "light"},
        )
        log.info(f"plug_apply: wrapped {entity_id} as a light")
    elif existing and not want:
        for e in existing:
            await hass.config_entries.async_remove(e.entry_id)
        log.info(f"plug_apply: unwrapped {entity_id} (no longer a light)")
