# pi-leds-off

Turns off a Raspberry Pi's onboard power and activity LEDs, at boot (`dtparam` lines in `/boot/firmware/config.txt`) and immediately (sysfs). Applied to Pis in bedrooms: `audio-bedroom`.

Undo: delete the `pi-leds-off` block from `config.txt` and reboot.
