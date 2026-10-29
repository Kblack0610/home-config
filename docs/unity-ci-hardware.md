# Unity CI runner hardware

What to buy or rent when adding a host to the Unity CI pool (`unity_ci_runners` in `ansible/inventory.yml`, admitted by [`ci-runner-governor`](../apps/ci-runner-governor/)). Hardware prices are used-market eBay asking prices seen 2026-10-29; recheck with the Sold filter before buying. The Mac Studio's slots are in [mac-machines.md](mac-machines.md#unity-ci-pool-mac-studio-until-it-is-sold).

## What the bottleneck is

CPU and heat. Measured on the workstation 2026-10-29: each batch Unity editor held 3.8-6.4 GB, memory pressure was ~0.5% over 5 minutes with 73 of 124 GB free, and the morning hold fired on load 48.9 on 24 cores and tctl 95.6 C with 7 editors running. RAM was never the limit.

Sizing per slot: 3 physical cores of its own and 14 GB RAM. Timed PlayMode tests fail when slots share cores, and while a batch editor sits at 4-6 GB, a PlayMode shard peaks at 13.9 GB and was OOM-killed at 14 GB four times between 10-26 and 10-28 (unity-core-playground `docs/operations/ci.md`, the hp-victus notes). Hyperthreads do not count as cores. A fast NVMe matters too, since every runner keeps its own `Library/` per project and a cold import is mostly disk.

Before buying for parallelism, check the license ceiling: the governor caps the pool at 16 admitted runners (8 before the Mac Studio joined, 2026-10-29), and about 15 Unity activations in 90 minutes is where activation trouble starts. At that cap a new host only helps by finishing each job faster, which favours one fast box over several slow ones.

## Checklist

- 6 cores / 12 threads minimum, 8+ preferred.
- Full-power desktop CPU. Skip `T`, `U` and `Y` suffixes (low-power parts). Laptop `H`/`HQ` is acceptable but throttles under sustained load.
- RAM: 14 GB per slot plus about 8 GB for the host: 32 GB for one slot, 48 GB for two (6 cores), 64 GB for three or four. DDR4 is far cheaper than DDR5 in 2026.
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
| OptiPlex 5060 SFF, i5-8500 / i5-8600 / i7-8700 (6 cores) | $80-100 plus RAM to 32-48 GB | About $6-8 per slot per month if two slots hold up, $10-14 if one (24 months, power included). 65 W, holds its clocks. Two slots on six cores leaves no core spare, so measure that before counting on it. Skip the i3-8100 (4 cores) |
| HP EliteDesk mini, Ryzen 5 PRO 2400GE / 3400GE (4 cores) | ~$60 plus 32 GB | One slot, about $8-10 per slot per month. 35 W 2018-19 chip, roughly half the per-core speed of the newer laptops, so a shard may run 1.5-2x longer. Often sold without the power brick |
| Used Dell Precision 7820, 16-24 core Xeon, 128 GB | $1,000-1,500 | 5-7 slots, about $12-18 per slot per month over 24 months. Cores about half the speed of current ones, 250-350 W, loud |
| Minisforum MS-A2 barebone ($799) plus 64 GB DDR5 SO-DIMM | ~$1,850-2,100 | 4 fast slots, about $21-24 per slot per month. Throttles about 11% under sustained load (Notebookcheck). The $1,335 listing is the 32 GB unit |

Buy one of a cheap model first, add it as one slot, and time a pinned PlayMode shard against hp-victus before buying more. Every home box is one more Unity activation (see [Unity license](#unity-license)).

## Laptops as runners

They work (asus-laptop, hp-victus and razer-laptop are already in the pool) but throttle under sustained load and their batteries swell when held at 100%. Set a 60-80% charge limit in the BIOS (or `thinkpad_acpi` thresholds on Linux) and run with the lid open.

## Renting

Checked against the providers' own catalogs and APIs on 2026-10-29. Per slot is the monthly price divided by slots at 3 cores and 14 GB each.

| Option | Monthly | Slots | Per slot | Notes |
|---|---|---|---|---|
| OVH RISE-5, Virginia (EPYC 7413, 24 cores, 128 GB, 2x960 GB NVMe) | $199, plus $199 setup or a 12-month prepay of $2,268.60 with none | 7 | ~$28 | Best rental. No term, high stock, 1-hour delivery |
| Cherry Servers, 2x EPYC 7443 (48 cores, 256 GB), Chicago | ~$425 (EUR 369) | 15 | ~$28 | Same price per slot, one box for two RISE-5s. One in stock |
| OVH RISE-3 / RISE-M (12-core Ryzen, 64 GB) | $118-123 | 3 | ~$40 | Smaller step |
| Hetzner AX102 (16 cores, 128 GB), EU only | EUR 259, plus EUR 129 setup | 5 | ~$60 | The LTD tiers are listed but not on sale, the server auction starts at EUR 208 for 10+ cores, and new Cloud CCX servers have been restricted since 2026-06-26 |
| RunsOn + AWS spot m7a.xlarge, us-east-2 ($0.075/h, under 5% interruption) | usage, plus RunsOn EUR 300/yr | elastic | ~$60-95 at 12 h/day | Scales to zero, but each job is a fresh activation and the Library cache would need building (sticky disks) |
| Managed per-minute: Ubicloud, Blacksmith, WarpBuild, Namespace, Depot, GitHub 4-core | usage | elastic | ~$160-260 | Cold Library on every job, no LAN. BuildJet has shut down; Cirrus takes no new customers |

Sources: OVH `/order/catalog/public/eco` and `/dedicated/server/datacenter/availabilities` (eu.api.ovh.com, api.us.ovhcloud.com), api.cherryservers.com/v1/plans, docs.hetzner.com price adjustment (15 June 2026) and the Hetzner live auction data, the AWS spot price and advisor feeds, runs-on.com/pricing, and each vendor's pricing page.

A rented box joins like a home one: tailnet first (`docs/headscale-setup.md`, so it reaches `git.kblab.me`), then `unity_ci_runners` and `ci_hosts` in `ansible/inventory.yml`, the "Unity CI runner pool" play (`unity-ci-host` and `github-actions-runner-linux`), and the three rosters kept in step (`RUNNER_ROSTER` in `apps/github-runner-exporter/deployment.yaml`, the governor's `hosts`, the inventory; `scripts/check-runner-roster.py` fails CI on drift). Start it at about half its slots, prove it with one pinned PlayMode shard timed against hp-victus, then ramp. Add one host at a time, so the new activations do not all start together.

The pool already registers as many slots as `pool_max_admitted` allows (16: asus 1, hp-victus 2, razer 5, mac-studio 8). A rented host adds parallel jobs only if the cap goes up, and the cap is there for the activation rate, so settle the license question first.

## Unity license

Every option above runs on the shared Unity Personal serial, and this is the owner's decision, still open on the 2026-10-29 sheet. Checked that day:

- Unity's Editor Terms (updated 2026-06-30), section 2.8: "you (or your Authorized User) may only use one instance at any given time per seat". It covers Personal, and the compliance FAQ says a build machine needs its own license.
- Section 2.5: Build Server licenses cannot be used with Personal.
- The 6000.3 docs say command-line serial activation, which `in-docker.sh` uses, does not apply to Personal. Nothing has blocked it, but Unity could close it, and then every slot fails at once.
- Compliant cost: Pro at about $2,310/yr per author, plus a Pro Build Server float per concurrent slot (about $740/yr from a reseller, unconfirmed), so roughly +$62 per slot per month, more than any hardware here. A Build License (10 build machines for about $2,310/yr) may be much cheaper if Unity sales confirm all 10 can run at once.

Sources: unity.com/legal/editor-terms-of-service/software, unity.com/pages/license-compliance, docs.unity.com (6000.3, managing your Unity license).

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
