# Drone 1 BOM: 3-inch ducted indoor quad, ArduPilot, 2S

Phase P1 of the self-docking drone plan (`~/.claude/plans/watn-to-print-and-radiant-puppy.md`). Prices were checked on 2026-11-08. A price marked "fetched" came from loading the product page. One marked "snippet" came from a search result because the store blocked automated fetches (Pyrodrone 429, GetFPV 403, Amazon 503, AliExpress login wall), so check it at checkout. Ordering is the human's job: nothing on this list has been bought.

## Frame

STLs are on disk at `~/3d-prints/drones/<design>/`, each folder with a README giving the source, author and license. Each design's largest part fits the Bambu A1's 256 mm bed.

| Rank | Design | Source | License | Stack holes (measured in the STL) | Motor holes | Why |
|---|---|---|---|---|---|---|
| 1 (pick) | CinePunk3 R3 by L3M4R | https://www.printables.com/model/511742-cinepunk3-r3-3d-printed-cinematic-quadcopter-frame | CC BY-NC (personal use is fine) | 20.5 mm square, M2 | 10.5 mm circle of oversize holes, to be opened slightly for a 9x9 or 12x12 motor | Light: the R2 was 60-90 g. It is modular, and it ships a blank camera plate you can remix into the Pi Zero and flow-sensor mounts. Needs no supports. The ducts are in TPU or PETG. |
| 2 | Stabylo by 8x8 | https://www.printables.com/model/157122-cinewhoop-fpv-drone-frame | CC BY | 20x20 M2, 20x20 M3 or 30x30 bottom plates | 12x12 M2 | Has the most complete listing, but it was flown on 1804 2800KV motors with an 1800 mAh pack. That makes it a heavier 4S-class airframe than a 2S build wants. Ducts need supports. |
| 3 | CineWhoop 3 Inch by Squint | https://www.thingiverse.com/thing:3555952 | CC BY | 20x20 or 16x16 top plate (snippet) | not confirmed | Not downloaded because Thingiverse returned 403. The designer had not built it when it was posted. |

Print settings, from the CinePunk listing: ducts in TPU (or PETG) at 10% infill, plates at 30-40% infill, spacers at 100%, M2 screws 4-8 mm long. Only one duct per side ships (`Duct_RF`, `Duct_RR`), so print each twice and mirror two copies in the slicer.

## Electronics

| Item | Pick | Store / URL | Price USD | Qty | Notes |
|---|---|---|---|---|---|
| FC + ESC (AIO) | Flywoo GOKU F405 HD 1-2S 12A ELRS AIO V2 | https://flywoo.net/products/goku-f405-hd-1-2s-elrs-aio-v2 (also https://www.defiancerc.com/products/flywoo-goku-f405-hd-1-2s-12a-elrs-aio-v2, $84.99 snippet) | 69.99 snippet | 1 | On ArduPilot's list: https://ardupilot.org/copter/docs/common-flywoof405hd.html (target `FlywooF405HD-AIOv2` on https://firmware.ardupilot.org/Copter/stable/). Takes 1-2S, has a 12A (25A peak) BLHeli_S ESC, ELRS on UART1, and a 5V 2A BEC that can feed the Pi. UART2 (MAVLink to the Pi) and UART3 (flow sensor) are free, with UART4 spare. Needs the ICM42688 gyro variant. See "Open questions" for the mounting pattern. |
| Motors | iFlight XING2 1404 4600KV (2-4S) | https://pyrodrone.com/products/iflight-xing2-1404-4600kv-motor | 20.99 each, snippet | 4 | Rated for 2S, which most 1404s are not. Nobody publishes 2S thrust figures. The estimate is about 100 g per motor (thrust scales with rpm squared, and a 4S figure of roughly 400 g per motor halves in rpm on 2S), so about 2:1 thrust-to-weight at roughly 200 g all-up. Check the hole pattern against the frame. |
| Props | HQProp T76MMX3 V2 cinewhoop, 1.5 mm shaft | https://www.hqprop.com/hqprop-t76mmx3-v2-for-cinewhoop-2cw2ccw-poly-carbonate-p0381.html (Pyrodrone $3.99, out of stock) | 2.79 per set, fetched | 3 sets | The T version fits the XING2's 1.5 mm shaft. The D76 is a 5 mm hub and does not fit. |
| Flow + lidar | MicoAir MTF-01 (ArduPilot firmware, or MTF-01P) | https://pyrodrone.com/products/micoair-mtf-01-optical-flow-8m-range-2in1-sensor (AliExpress MTF-01P $23.83 snippet: https://www.aliexpress.us/item/3256808610998866.html) | 32.99 snippet | 1 | ArduPilot doc: https://ardupilot.org/copter/docs/common-mtf-01.html. Runs MAVLink: `FLOW_TYPE=5`, `RNGFND1_TYPE=10`, `RNGFND1_ORIENT=25`, on a serial port with `SERIALn_PROTOCOL=1` at 115200. On 4.5+, set the sensor's `mav_id=200`. Range is 2 cm to 8 m. Needs at least 60 lux. |
| Companion | Raspberry Pi Zero 2 W (no header) | https://www.pishop.us/product/raspberry-pi-zero-2-w/ (Adafruit https://www.adafruit.com/product/5291, $19.05) | 17.25 fetched | 1 | Out of stock at both stores on 2026-11-08, so watch rpilocator. Solder the UART and power wires to the pads; no header needed. |
| Camera | Raspberry Pi Camera Module 3, standard 75 degree lens | https://www.adafruit.com/product/5657 | 29.25 fetched | 1 | In stock. |
| Camera cable | Pi Zero camera FPC cable (22-pin to 15-pin) | Adafruit, offered on the camera page | 3.95 | 1 | The Zero needs the narrow 22-pin connector. |
| microSD | 32 GB class 10 / A1 | https://www.pishop.us/product/class-10-microsd-card-32-gb-blank-retail/ | 14.95 fetched | 1 | Out of stock there; any SanDisk 32 GB card works. |
| ELRS receiver | none needed | - | 0 | 0 | ELRS is built into the AIO. RadioMaster RP1 V2 ($24.99 fetched, https://www.racedayquads.com/products/radiomaster-rp1-v2-2-4ghz-elrs-nano-receiver) only if the AIO choice changes. |
| ELRS transmitter | RadioMaster Pocket, ELRS | https://www.racedayquads.com/products/radiomaster-pocket-edgetx-rc-transmitter-choose-version | 84.49 fetched | 1 | This is the safety link and kill switch. It takes 2x 18650 cells, which are not included. Stock varies by variant. |
| 18650 cells | 2x protected or flat-top 18650 | any | about 12 (unpriced) | 2 | For the Pocket. |
| Battery | Tattu 450 mAh 2S 75C XT30, long, about 28 g | https://wrekd.com/products/tattu-450mah-2s-75c-7-4v-lipo-battery-pack-with-xt30-plug-long-size | 7.22 snippet | 2 | Standard 4.2 V per cell (avoid HV packs), which matches the IP2326. Alternative: Tattu R-Line 550 mAh 2S (https://genstattu.com/tattu-550mah-2s-7-4v-r-line-lipo-battery-long-pack-with-xt30-plug/). |
| Onboard charger | IP2326 5V-in 2S 8.4V boost balance charger module | https://us.amazon.com/IP2326-Charging-Module-Support-Balancing/dp/B0GTNBCCQM (alt https://www.aliexpress.us/item/3256808965113588.html) | not checked (Amazon 503) | 2 | 4.5-9.5 V in, up to about 1.5 A, balances 2S. Listings disagree on power (15 W vs 19 W), so check the board you get. The spare one is for bench charging. Charge on a non-flammable tile, attended. |
| Dock contacts | Adafruit pogo pin header, 9-pin inline 0.1 in | https://www.adafruit.com/product/5381 | 1.95 fetched | 2 | In stock. Cut it into 2-pin pieces for the legs. Copper tape covers the dock-side plates. Alternative: a 2-pin magnetic pogo pair (https://www.amazon.com/Magnetic-Connector-Pogopin-Through-Connectors/dp/B0CSX6ZQ1H, snippet, 1-2 A). |
| Hardware | M2 screw assortment 4-8 mm, XT30 pigtails, 26/28 AWG silicone wire | any | about 15 (unpriced) | 1 | M2 is all the frame uses. |
| Filament | TPU 95A (ducts) and PETG (plates) | already on hand | 0 | - | |

## Totals and weight

- Priced items: about $364, made up of the AIO $69.99, motors $83.96, props $8.37, MTF-01 $32.99, Pi Zero 2 W $17.25, camera $29.25, cable $3.95, microSD $14.95, Pocket $84.49, packs $14.44 and pogo headers $3.90.
- Unpriced items (two IP2326 boards, 18650s, hardware): add about $35, for a total of about $400.
- That is above the plan's $250-300 estimate. The Pocket came in at $84 against $60 estimated, and the motors at $84 against $40.
- Without the transmitter the total is about $315.
- All-up weight estimate: about 200 g. That is frame 60-90 g, AIO 5 g, motors 4 x 9 g, props 8 g, MTF-01 3 g, Pi Zero 2 W 10 g, camera and cable 5 g, pack 28 g, plus wiring, charger and pogos 13 g.
- This is an estimate; nothing has been weighed yet. Expect 3-5 minutes of hover, as the plan says.

## Open questions

- **AIO mounting pattern.** The ArduPilot doc gives the GOKU F405 HD's holes as "20.5 x 20.5", which would match the CinePunk3's measured 20.5 mm stack holes. Retailers (Rotor Riot, Pyrodrone) list it as 25x25 or 25.5x25.5, the usual whoop AIO pattern. Measure the board when it arrives. If it is 25.5, print a 20.5-to-25.5 adapter plate (one small part) or open up the bottom plate.
- **2S versus 3S.** No ArduPilot-listed F405 20x20 stack in stock is rated for 2S. The SpeedyBee F405 Mini stack (target `SpeedyBeeF405Mini`, 20x20, about $63-70) is 3-6S and discontinued at SpeedyBee. The MicoAir405Mini is discontinued too. The 2S path therefore leans on this AIO. Moving to 3S later means a new FC, a 3S charger board and new packs. The XING2 motors are rated 2-4S, so they carry over.
- **Thrust margin.** No 2S thrust data exists for the XING2 1404 4600KV on a 3-inch prop. Phase P2's first hover answers it. If it hovers above about 70% throttle, move to 3S.
- **Stock.** The Pi Zero 2 W and the MTF-01 were out of stock or unverifiable at the stores checked.
