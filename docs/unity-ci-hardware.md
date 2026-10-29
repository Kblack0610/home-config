# Unity CI runner hardware

What to buy when adding a host to the Unity CI pool (`unity_ci_runners` in `ansible/inventory.yml`, admitted by [`ci-runner-governor`](../apps/ci-runner-governor/)). Prices are used-market eBay asking prices seen 2026-10-29; recheck with the Sold filter before buying.

## What the bottleneck is

CPU and heat. Measured on the workstation 2026-10-29: each batch Unity editor held 3.8-6.4 GB, memory pressure was ~0.5% over 5 minutes with 73 of 124 GB free, and the morning hold fired on load 48.9 on 24 cores and tctl 95.6 C with 7 editors running. RAM was never the limit.

Sizing per concurrent Unity job: about 6-8 GB RAM and 2-4 CPU threads, more during IL2CPP and shader compiles. A fast NVMe matters too, since every runner keeps its own `Library/` per project and a cold import is mostly disk.

Before buying for parallelism, check the license ceiling: the governor caps the pool at 8 admitted runners, and about 15 Unity activations in 90 minutes is where activation trouble starts. At that cap a new host only helps by finishing each job faster, which favours one fast box over several slow ones.

## Checklist

- 6 cores / 12 threads minimum, 8+ preferred.
- Full-power desktop CPU. Skip `T`, `U` and `Y` suffixes (low-power parts). Laptop `H`/`HQ` is acceptable but throttles under sustained load.
- RAM sized to cores (~6-8 GB per job): 32 GB for 8 cores, 48-64 GB for 12. Do not pay extra for 64 GB on a 4-6 core box. DDR4 is far cheaper than DDR5 in 2026.
- NVMe SSD, 1 TB or more.
- Wired Ethernet, and a case that can cool a sustained load.
- Condition "Used" or "Refurbished" with returns. Avoid "for parts", all-in-ones (laptop CPUs, often no video out, soldered RAM) and "up to" variation listings (the headline price is the base config).
- The Windows license does not matter; the host gets Linux.
- Either vendor works: the governor reads both `k10temp` (AMD) and `coretemp` (Intel) (`apps/ci-runner-governor/governor.py:64-65`).

## AMD or Intel

- Ryzen 5000 (Zen 3, AM4) is the sweet spot: most cores per dollar on cheap DDR4. Targets: Ryzen 7 5700X / 5800X (8C/16T), Ryzen 9 5900X (12C/24T, ~$200 used CPU).
- Intel 8th-10th gen desktop is fine and cheap, but fewer cores per dollar.
- Avoid used Intel 13th/14th gen i7/i9: the Raptor Lake degradation issue means a used chip may already be damaged even with the microcode fix.
- New platforms (Core Ultra 200, Ryzen 7000/9000) need DDR5, which is expensive in 2026.

Reading model names: `i3/i5/i7/i9` and `Ryzen 3/5/7/9` are tiers and say nothing about age. The generation is the leading digit(s) of the model number (`i7-9700` is 9th gen, `i7-1355U` is 13th, `Ryzen 9 5900X` is the 5000 series).

## Options by value

| Option | Typical price | Notes |
|---|---|---|
| OptiPlex 7060 Micro / ThinkCentre M920q (i7-8700T, 6C/12T) | $100-180 with RAM | Cheapest usable; 35 W so slower |
| HP Z4 G4, Xeon W-2145 (8C/16T) | ~$190-250 with 32 GB | Best cores per dollar in a used workstation. Skip W-2102/2104/2123/2125/2223 (4 cores). Needs a discrete GPU (Xeon W has no iGPU). ECC RDIMM. Higher idle power |
| Used Ryzen 9 5900X build on AM4 | ~$450-600 total | Fastest per dollar, about 2x a 6-core laptop (estimate). Needs a cheap GPU for setup |

## Laptops as runners

They work (asus-laptop, hp-victus and razer-laptop are already in the pool) but throttle under sustained load and their batteries swell when held at 100%. Set a 60-80% charge limit in the BIOS (or `thinkpad_acpi` thresholds on Linux) and run with the lid open.

## Listings reviewed 2026-10-29

| Listing | Price | Verdict |
|---|---|---|
| ThinkPad X1 Extreme Gen 2, i7-9750H (6C/12T), 64 GB | $207 auction, still live | Best laptop for CI; worth up to ~$300. Thermally limited |
| Dell Latitude 5540, i7-1355U/1365U, 64 GB DDR5 | $370 | Good price (the DDR5 is the value) but a 15 W chip; better as a daily laptop. The 5540 is 15.6", not 14" |
| HP desktop, i7 8th gen, 64 GB, 1 TB NVMe | not given | OK if an i7-8700 under ~$250-300; an 8700T is mini-PC tier |
| HP desktop, "up to" i7 9th gen, 32 GB, 4 TB | $170 | $170 is the base variation; good only if the i7-9700 option is near that price |
| Precision 7520, i7-6820HQ (4C/8T), 64 GB | not given | Weakest; only under ~$150 for light jobs |
| HP All-in-One, Ryzen 5, 16 GB, broken screen, for parts | $0.99 bid (reserve not met), $325 BIN | Skip; laptop CPU, maybe no video out, no returns. Bid only with a ~$50 cap |
| 2x HP Z4 G4, Xeon W-2223 (4C/8T), 16 GB, Quadro P2000 | $500 for both | Skip; 8 cores total across two boxes, about two slow jobs each, spends license activations. One 5900X build or one W-2145/W-2155 Z4 does more for the money |
