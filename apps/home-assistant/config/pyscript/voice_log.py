"""Voice log: one logbook line per spoken turn, against the satellite that heard it.

The recorder already keeps every satellite state change (listening, processing,
responding) but not what was said. This subscribes to HA's conversation chat logs and
writes "heard: ... / replied: ..." with logbook.log, so the Event log view
(dashboards/view_log.yaml) shows the words next to the device that heard them.

The chat log carries no device, so the source is the satellite that is listening or
processing when the user's words arrive. If none is, the turn was typed (HA chat,
companion app) and the line is logged as "Typed" with no entity.

Lines are kept by the recorder like any logbook entry: default 10 days, local only.
"""

UNSUB_KEY = "voice_log_unsub"


@pyscript_compile
def _make_listener(hass):
    """Native callback for async_subscribe_chat_logs; it runs in the event loop."""
    import homeassistant.helpers.area_registry as ar
    import homeassistant.helpers.device_registry as dr
    import homeassistant.helpers.entity_registry as er

    pending = {}

    def source():
        hearing = [
            s for s in hass.states.async_all("assist_satellite")
            if s.state in ("listening", "processing")
        ]
        if not hearing:
            return None
        return max(hearing, key=lambda s: s.last_changed).entity_id

    def room(entity_id):
        entry = er.async_get(hass).async_get(entity_id)
        area_id = entry and entry.area_id
        if entry and not area_id and entry.device_id:
            device = dr.async_get(hass).async_get(entry.device_id)
            area_id = device and device.area_id
        area = area_id and ar.async_get(hass).async_get_area(area_id)
        if area:
            return area.name
        state = hass.states.get(entity_id)
        return state.name if state else entity_id

    def on_chat(conversation_id, event_type, data):
        if event_type == "deleted":
            pending.pop(conversation_id, None)
            return
        if event_type != "content_added":
            return
        content = data.get("content") or {}
        role = content.get("role")
        if role == "user":
            pending[conversation_id] = (content.get("content", ""), source())
        elif role == "assistant" and content.get("content") and conversation_id in pending:
            heard, entity_id = pending.pop(conversation_id)
            entry = {
                "name": room(entity_id) if entity_id else "Typed",
                "message": f"heard: {heard} / replied: {content['content']}",
            }
            if entity_id:
                entry["entity_id"] = entity_id
            hass.async_create_task(hass.services.async_call("logbook", "log", entry))

    return on_chat


@time_trigger("startup")
def voice_log_startup():
    """Subscribe once; a pyscript reload drops the previous subscription first."""
    old = hass.data.pop(UNSUB_KEY, None)
    if old:
        old()
    try:
        from homeassistant.components.conversation.chat_log import async_subscribe_chat_logs

        hass.data[UNSUB_KEY] = async_subscribe_chat_logs(hass, _make_listener(hass))
    except Exception as err:
        log.warning(f"voice_log: cannot subscribe to chat logs, transcripts are not logged: {err}")
        return
    log.info("voice_log: subscribed to chat logs")
