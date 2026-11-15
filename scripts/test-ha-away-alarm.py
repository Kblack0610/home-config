"""Run in the Home Assistant image: python test-ha-away-alarm.py away_alarm.yaml."""

import asyncio
from datetime import timedelta
from pathlib import Path
import sys
import tempfile

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant import bootstrap, loader
from homeassistant.util import dt as dt_util
import yaml


async def main():
    package = yaml.safe_load(Path(sys.argv[1]).read_text())
    automations = {a["id"]: a for a in package["automation"]}
    with tempfile.TemporaryDirectory(prefix="away-alarm-test-") as config:
        hass = HomeAssistant(config)
        arm = automations["away_alarm_arm"]
        guard = Template(arm["trigger"][0]["value_template"], hass)
        recheck = Template(arm["condition"][0]["value_template"], hass)
        disarm = Template(automations["away_alarm_disarm"]["trigger"][0]["value_template"], hass)
        cases = [
            ("0", [], False),
            ("0", ["unknown", "unknown"], False),
            ("0", ["not_home", "unknown"], False),
            ("0", ["unavailable", "not_home"], False),
            ("unknown", ["not_home", "not_home"], False),
            ("unavailable", ["not_home", "not_home"], False),
            ("1", ["home", "not_home"], False),
            ("0", ["not_home", "not_home"], True),
            ("0", ["Work", "not_home"], True),
        ]
        for zone, people, expected in cases:
            hass.states.async_set("zone.home", zone)
            for old in list(hass.states.async_all("person")):
                hass.states.async_remove(old.entity_id)
            for i, state in enumerate(people):
                hass.states.async_set(f"person.test_{i}", state)
            assert guard.async_render() == expected, (zone, people, expected)
            assert recheck.async_render() == expected, (zone, people, expected)
            assert disarm.async_render() == (zone == "1"), zone

        cooldown = Template(automations["away_alarm_bark"]["condition"][1]["value_template"], hass)
        for seconds, expected in [(None, True), (899, False), (900, True), (901, True)]:
            last = None if seconds is None else dt_util.utcnow() - timedelta(seconds=seconds)
            assert cooldown.async_render({"this": {"attributes": {"last_triggered": last}}}) == expected, seconds

        alerts = []

        async def home_alert(call):
            alerts.append(dict(call.data))

        hass.services.async_register("script", "home_alert", home_alert)
        hass.states.async_set("zone.home", "0")
        hass.states.async_set("person.test_0", "unknown")
        cameras = ["binary_sensor.amcrest_ptz_1_person_occupancy", "binary_sensor.amcrest_living_room_person_occupancy"]
        for entity in [*cameras, "binary_sensor.dog_barking"]:
            hass.states.async_set(entity, "off")
        loader.async_setup(hass)
        assert await bootstrap.async_from_config_dict(package, hass) is hass
        await hass.async_start()
        await hass.async_block_till_done()
        assert hass.states.get("alarm_control_panel.away").state == "disarmed"

        async def alarm(service):
            await hass.services.async_call("alarm_control_panel", service, {"entity_id": "alarm_control_panel.away"}, blocking=True)
            await hass.async_block_till_done()

        for camera in cameras:
            await alarm("alarm_arm_away")
            assert hass.states.get("alarm_control_panel.away").state == "armed_away"
            assert alerts[-1]["priority"] == "low"
            hass.states.async_set(camera, "on")
            await hass.async_block_till_done()
            assert hass.states.get("alarm_control_panel.away").state == "triggered"
            assert alerts[-1]["priority"] == "high"
            await alarm("alarm_disarm")
            assert alerts[-1]["priority"] == "low"
            hass.states.async_set(camera, "off")
            await hass.async_block_till_done()

        await alarm("alarm_arm_away")
        hass.states.async_set("binary_sensor.dog_barking", "on")
        await hass.async_block_till_done()
        assert alerts[-1]["priority"] == "default"
        assert hass.states.get("alarm_control_panel.away").state == "armed_away"
        count = len(alerts)
        last = hass.states.get("automation.away_alarm_dog_barking").attributes["last_triggered"]
        hass.states.async_set("binary_sensor.dog_barking", "off")
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.dog_barking", "on")
        await hass.async_block_till_done()
        assert len(alerts) == count
        assert hass.states.get("automation.away_alarm_dog_barking").attributes["last_triggered"] == last
        await alarm("alarm_disarm")
        count = len(alerts)
        hass.states.async_set(cameras[0], "on")
        await hass.async_block_till_done()
        assert len(alerts) == count
        assert hass.states.get("alarm_control_panel.away").state == "disarmed"
        count = len(alerts)
        await alarm("alarm_arm_away")
        assert hass.states.get("alarm_control_panel.away").state == "triggered"
        assert any(a["priority"] == "high" for a in alerts[count:])
        await alarm("alarm_disarm")
        hass.states.async_set(cameras[0], "off")
        await hass.async_block_till_done()
        await alarm("alarm_arm_away")
        hass.states.async_set("zone.home", "1")
        await hass.async_block_till_done()
        assert hass.states.get("alarm_control_panel.away").state == "disarmed"
        assert alerts[-1]["priority"] == "low"
        await hass.async_stop()
    print("PASS: 9 presence cases, 4 cooldown boundaries, both cameras/on-arm trigger high alerts, bark default/suppression, disarm low/no person alert, return disarms")


if __name__ == "__main__":
    asyncio.run(main())
