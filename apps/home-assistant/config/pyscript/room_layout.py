"""Room layout seed: applies pyscript/room_layout.yaml to HA's native registries.

Home Assistant has no built-in "set area / set name" service. A pyscript function
runs in HA's own context, so it can write the registries. All metadata lives there:
    room      -> core.area_registry + entity.area_id
    name      -> core.entity_registry name override
    icon      -> core.entity_registry icon override
    labels    -> core.label_registry + entity.labels (lamps.yaml groups by "Lamp")

Renames and room moves are made in room_layout.yaml, not from the wall.
"""

import homeassistant.helpers.entity_registry as er
import homeassistant.helpers.area_registry as ar
import homeassistant.helpers.label_registry as lr
import homeassistant.helpers.device_registry as dr
import pathlib
import yaml

# Baseline appliance categories, materialised as HA labels on startup.
CATEGORIES = ["Lamp", "Appliance", "Fan", "Heater", "Printer"]

# Git-managed room-layout seed (areas + initial per-thing room assignments).
LAYOUT_PATH = "/config/pyscript/room_layout.yaml"


@time_trigger("startup")
def room_labels_reconcile():
    """Ensure the baseline category labels exist (idempotent, non-destructive)."""
    reg = lr.async_get(hass)
    have = {lab.name for lab in reg.async_list_labels()}
    created = 0
    for name in CATEGORIES:
        if name not in have:
            reg.async_create(name=name)
            created += 1
    log.info(f"room_layout: reconcile done (+{created} category labels)")


def _do_room_seed(force):
    """Seed areas + per-entity/-device rooms from room_layout.yaml.

    NON-DESTRUCTIVE unless force: a thing gets a room only when it has none, and
    a name, icon or label only when there is none yet, so manual edits in the HA UI
    always win. force=True re-applies the seed over everything.
    """
    # Read off the event loop. task.executor rejects pyscript-compiled functions,
    # but Path.read_text is a real bound method, so it runs cleanly in the
    # executor (avoids HA's "blocking call inside the event loop" warning).
    try:
        raw = task.executor(pathlib.Path(LAYOUT_PATH).read_text)
    except FileNotFoundError:
        log.warning(f"room_layout_seed: {LAYOUT_PATH} missing")
        return
    layout = yaml.safe_load(raw) or {}
    if not layout:
        log.warning(f"room_layout_seed: {LAYOUT_PATH} empty")
        return

    area_reg = ar.async_get(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    def _area_id(name):
        area = area_reg.async_get_area_by_name(name) or area_reg.async_create(name)
        return area.id

    def _label_id(name):
        reg = lr.async_get(hass)
        label = reg.async_get_label_by_name(name) or reg.async_create(name=name)
        return label.label_id

    # 1) Ensure areas exist (create-if-missing, non-destructive).
    made = 0
    for name in layout.get("areas") or []:
        if area_reg.async_get_area_by_name(name) is None:
            area_reg.async_create(name)
            made += 1

    # 2) Per-entity room, name, icon, aliases and labels.
    ent_set = 0
    for entity_id, spec in (layout.get("entities") or {}).items():
        entry = ent_reg.async_get(entity_id)
        if entry is None:
            log.warning(f"room_layout_seed: {entity_id} not in entity registry")
            continue
        updates = {}
        room = spec.get("room")
        if room and (force or entry.area_id is None):
            updates["area_id"] = _area_id(room)
        name = spec.get("name")
        if name and (force or not entry.name):
            updates["name"] = name
        icon = spec.get("icon")
        if icon and (force or not entry.icon):
            updates["icon"] = icon
        # Extra spoken names for Assist. COMPUTED_NAME keeps the entity's own
        # name answering too; only set while the entity has no custom alias.
        aliases = spec.get("aliases") or []
        custom = [a for a in entry.aliases if a is not er.COMPUTED_NAME]
        if aliases and (force or not custom):
            updates["aliases"] = [er.COMPUTED_NAME] + list(aliases)
        labels = spec.get("labels") or []
        if labels and (force or not entry.labels):
            updates["labels"] = set([_label_id(n) for n in labels])
        if updates:
            ent_reg.async_update_entity(entity_id, **updates)
            ent_set += 1

    # 3) Whole-device room (vacuum, litter box, ...) resolved from a
    #    representative entity_id -> its device (moves all its entities at once).
    dev_set = 0
    for entity_id, spec in (layout.get("devices") or {}).items():
        entry = ent_reg.async_get(entity_id)
        if entry is None or entry.device_id is None:
            log.warning(f"room_layout_seed: {entity_id} has no device")
            continue
        device = dev_reg.async_get(entry.device_id)
        room = spec.get("room")
        if device and room and (force or device.area_id is None):
            dev_reg.async_update_device(device.id, area_id=_area_id(room))
            dev_set += 1

    log.info(
        f"room_layout_seed: areas+{made} entities={ent_set} devices={dev_set} "
        f"force={force}"
    )


@service
def room_layout_seed(force=False):
    """Apply the git-managed room layout (pyscript.room_layout_seed).

    force=false (default) only fills in unassigned things; force=true re-applies
    every mapping in room_layout.yaml, overriding current assignments.
    """
    _do_room_seed(force)


@time_trigger("startup")
def room_layout_seed_startup():
    """Seed the room layout on every boot (non-destructive)."""
    _do_room_seed(False)
