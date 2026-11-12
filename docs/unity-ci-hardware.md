# Unity CI runner hardware

What to buy or rent when adding a host to the Unity CI pool (`unity_ci_runners` in `ansible/inventory.yml`, admitted by [`ci-runner-governor`](../apps/ci-runner-governor/)). Hardware prices are used-market eBay asking prices seen 2026-10-29; recheck with the Sold filter before buying. The Mac Studio's slots are in [mac-machines.md](mac-machines.md#unity-ci-pool-mac-studio-until-it-is-sold). Operating a pool host (the play, activation, refreshing its copies, labels): [runbooks/unity-ci-host.md](runbooks/unity-ci-host.md).

## What the bottleneck is

CPU and heat. Measured on the workstation 2026-10-29: each batch Unity editor held 3.8-6.4 GB, memory pressure was ~0.5% over 5 minutes with 73 of 124 GB free, and the morning hold fired on load 48.9 on 24 cores and tctl 95.6 C with 7 editors running. RAM was never the limit.

Sizing per slot: 3 physical cores of its own and 14 GB RAM. Timed PlayMode tests fail when slots share cores, and while a batch editor sits at 4-6 GB, a PlayMode shard peaks at 13.9 GB and was OOM-killed at 14 GB four times between 10-26 and 10-28 on hp-victus. Hyperthreads do not count as cores. A fast NVMe matters too, since every runner keeps its own `Library/` per project and a cold import is mostly disk.

Before buying for parallelism, check the license ceiling: the governor caps the pool at `pool_max_admitted` admitted runners (24 in `apps/ci-runner-governor/config.json`), and about 15 Unity activations in 90 minutes is where activation trouble starts. At that cap a new host only helps by finishing each job faster, which favours one fast box over several slow ones.

## Checklist

- 6 cores / 12 threads minimum, 8+ preferred.
- Full-power desktop CPU. Skip `T`, `U` and `Y` suffixes (low-power parts). Laptop `H`/`HQ` is acceptable but throttles under sustained load.
- RAM: 14 GB per slot plus about 8 GB for the host: 32 GB for one slot, 48 GB for two (6 cores), 64 GB for three or four. DDR5 costs about $14-16 per GB in the 2026 shortage, desktop or SODIMM, so RAM you already own is the cheapest slot; see [Buying in the RAM shortage](#buying-in-the-ram-shortage).
- NVMe SSD, 1 TB or more.
- Wired Ethernet, and a case that can cool a sustained load.
- Condition "Used" or "Refurbished" with returns. Avoid "for parts", all-in-ones (laptop CPUs, often no video out, soldered RAM) and "up to" variation listings (the headline price is the base config).
- The Windows license does not matter; the host gets Linux.
- Either vendor works: the governor reads both `k10temp` (AMD) and `coretemp` (Intel) (`apps/ci-runner-governor/governor.py:64-65`).

## AMD or Intel

- Ryzen 5000 (Zen 3, AM4) is the sweet spot: most cores per dollar on cheap DDR4. Targets: Ryzen 7 5700X / 5800X (8C/16T), Ryzen 9 5900X (12C/24T, ~$200 used CPU).
- Intel 8th-10th gen desktop is fine and cheap, but fewer cores per dollar.
- Avoid used Intel 13th/14th gen i7/i9: the Raptor Lake degradation issue means a used chip may already be damaged even with the microcode fix.
- Ryzen 9000 (AM5) on desktop DDR5 now costs about the same as AM4 on DDR4, with faster cores and a built-in GPU (2026-11-10 prices below).

Reading model names: `i3/i5/i7/i9` and `Ryzen 3/5/7/9` are tiers and say nothing about age. The generation is the leading digit(s) of the model number (`i7-9700` is 9th gen, `i7-1355U` is 13th, `Ryzen 9 5900X` is the 5000 series).

## Options by value

| Option | Typical price | Notes |
|---|---|---|
| OptiPlex 7060 Micro / ThinkCentre M920q (i7-8700T, 6C/12T) | $100-180 with RAM | Cheapest usable; 35 W so slower |
| HP Z4 G4, Xeon W-2145 (8C/16T) | ~$190-250 with 32 GB | Best cores per dollar in a used workstation. Skip W-2102/2104/2123/2125/2223 (4 cores). Needs a discrete GPU (Xeon W has no iGPU). ECC RDIMM. Higher idle power |
| Ryzen 9 5900X build on AM4 | ~$1,240 with 64 GB new (2026-11-10) | Was ~$450-600 before the RAM shortage. Needs a cheap GPU for setup |
| OptiPlex 5060 SFF, i5-8500 / i5-8600 / i7-8700 (6 cores) | $80-100 plus RAM to 32-48 GB | About $6-8 per slot per month if two slots hold up, $10-14 if one (24 months, power included). 65 W, holds its clocks. Two slots on six cores leaves no core spare, so measure that before counting on it. Skip the i3-8100 (4 cores) |
| HP EliteDesk mini, Ryzen 5 PRO 2400GE / 3400GE (4 cores) | ~$60 plus 32 GB | One slot, about $8-10 per slot per month. 35 W 2018-19 chip, roughly half the per-core speed of the newer laptops, so a shard may run 1.5-2x longer. Often sold without the power brick |

Buy one of a cheap model first, add it as one slot, and time a pinned PlayMode shard against razer-laptop before buying more. Every home box is one more Unity activation (see [Unity license](#unity-license)).

## Buying in the RAM shortage

Researched 2026-11-10 to replace the Mac Studio's slots (sold that day; the AWS hosts hold them meanwhile, aws-burst-2 on-demand since 2026-11-11). That load is about 5 slots around the clock, so every rental reaches a home box's price in 2-11 months (full comparison: `~/.agent/research/2026-11-10_ci-runners-buy-vs-rent.md`).

RAM sets the price. A 96 GB DDR5 SODIMM kit was $195 in May 2025 and is $1,280-1,700 now; the only in-stock kit seen on a fetched page was Corsair CMSX96GX5M2A4800C40 at $1,467.99 (corsair.com). Desktop DDR5 is no cheaper per GB: the cheapest new 64 GB (2x32 GB) kit is $870-930 (Corsair DDR5-5600 CL40 $869.99 at Newegg per bytesizeddeals, Corsair Vengeance DDR5-6000 CL40 $929.99 at Newegg per pgrid, checked 2026-11-10), against $1,468 for 96 GB of SODIMM; an earlier $520 figure (rampricesusa) was wrong. A 32 GB (2x16 GB) DDR5-5600 SODIMM kit is $520-585 (Crucial CT2K16G56C46S5, Amazon $522.85 on 2026-11-06). 64 GB DDR4-3200 was $446 and has not been rechecked. DDR4 server RDIMM is no longer cheap: 32 GB is $300-385 (TechMikeNY, PCSP), so the RDIMM builds below now cost $1,000-1,500 more than listed. TrendForce sees prices high through 2027, and Micron sees supply tight through 2028.

| Build | Slots | All-in | Multi / single vs MS-A2 | Notes |
|---|---|---|---|---|
| Ryzen 9 9900X (12c) AM5 tower, 64 GB DDR5 | 4 | ~$1,760-1,820 | 96% / 106% | Built-in GPU, current platform. CPU $331, RAM $870-930 |
| Ryzen 9 9950X (16c) AM5, 64 GB DDR5 | 4, 5 with 96 GB | ~$1,970-2,030 | 117% / 108% | CPU $539. 96 GB desktop DDR5 price unverified ($440-1,860 listed) |
| Ryzen 9 5900XT (16c) AM4, 64 GB DDR4 | 4 | ~$1,330 | 78% / 79% | CPU $315 (Amazon, in stock). No cooler or GPU in the box, add ~$65 |
| Ryzen 9 5950X AM4, 128 GB DDR4 | 5 | ~$1,780-1,950 | 80% / 79% | 4x32 DDR4 $831-1,000 |
| Ryzen 9 9900X in an ASRock DeskMeet X600 (8 L), 64 GB DDR5 | 4 | ~$1,725-1,785 | ~85% (estimate) / ~106% | Barebone $179 (Newegg N82E16856158094), $195-232 (Best Buy) with the board and a 500 W PSU; 4 standard DDR5 DIMM slots. 65 W CPU limit, so the 9900X runs in 65 W ECO mode; needs a cooler under 54 mm (~$50) |
| **Ryzen 9 9900X in an ASRock DeskSlim X600 (5 L), 64 GB DDR5** | 4 | ~$1,750-1,810 | 96% / 106% | **Recommended small box.** Barebone $209.99 (Newegg N82E16856158101, search snippet), $214.49 new on eBay. 4 standard DDR5 DIMM slots, CPUs up to 120 W so the 9900X runs at full power, coolers up to 71 mm, 240 W adapter |
| **MS-A2 9955HX barebone with razer-laptop's 96 GB SODIMM** | 5 | ~$1,600-1,665 | 100% / 100% | **Best value.** Barebone $789, 2 TB $290, and a 32 GB SODIMM kit ($520-585) so razer-laptop keeps its 1 Unity slot. razer-laptop (8945HS, 8 cores) is CPU-bound at 1-2 slots and used 18 of 93 GB on 2026-11-10. Confirm its RAM is two removable SODIMMs before buying |
| MS-A2 9955HX, 96 GB DDR5 SODIMM | 5 | ~$2,550 | 100% / 100% | 96 GB and 64 GB bundles sold out; barebone $789 plus the Corsair kit |
| windows-media CPU swap to a 5900XT or 5950X | 3-4 | ~$350-435 | 78-80% / 79% | Cheapest, but shares the TV PC and needs WSL2 or a VM (see below) |

All-in is CPU, board ($110-130), RAM, 2 TB NVMe ($290), cooler, PSU and case ($140). CPU and RAM prices were on fetched pages; boards, cases and every eBay figure are search snippets (eBay, Newegg, Micro Center and B&H refused fetches). Multi and single are PassMark against the MS-A2's 56,381 / 4,391. AM4 and AM5 draw about 190-200 W at the wall under load, against 135-160 W for the MS-A2; ECO mode trades about 10% for much less.

Small boxes that take desktop DDR5 (checked 2026-11-10): the DeskMeet X600 and DeskSlim X600 above are the only ASRock barebones with standard DIMM slots. The DeskMini X600 and X600TM-ITX use SODIMM, which puts them back at SODIMM prices. A mini-ITX AM5 board in a small case is the other route. DDR4 SODIMM minis save nothing: a 64 GB DDR4-3200 SODIMM kit is $420-550 new (Crucial CT2K32G4SFD832A $496.73 on Amazon 2026-11-04, $549 at B&H; it was $92 in March 2025), about the same as 64 GB of desktop DDR5. Those minis also top out at 8 cores (Ryzen 9 5900HX barebones $280-460), which is 2 slots, so 4 slots means two boxes and two Unity activations for about the same money as one 9900X. Most i9-12900H minis (MS-01, GTi12) take DDR5 SODIMM. The DeskMeet ECO-mode figure is an estimate for Zen 5 at 65 W, not measured in that box, and ASRock's per-model CPU list was not fetched. Sources: [ASRock DeskMeet X600](https://www.asrock.com/nettop/AMD/DeskMeet%20X600%20Series/index.asp), [ASRock DeskSlim X600](https://www.asrock.com/nettop/AMD/DeskSlim%20X600%20Series/index.asp), [Best Buy DeskMeet X600](https://www.bestbuy.com/product/asrock-deskmeet-x600-barebone-mini-pc-amd-am5-ryzen-9000-8000-7000-65w-4x-ddr5-7200-mhz/C34K265SRC/sku/12937025).

windows-media (Ryzen 5 5500, 6 cores, 64 GB, ASUS TUF GAMING B550-PLUS WIFI II) gives one slot as it is: two slots on six cores leave nothing for the TV. A 16-core AM4 CPU in the same board makes 3-4 slots on the RAM it already has. The board's BIOS 3645 (2026-09-14, AGESA ComboV2 PI 1.2.0.12) supports the 5900XT; it is not on the PC's 3607, so the flash comes first. The pool's runners are Linux containers, so it needs WSL2, which needs SVM on, and it is the first pool host that is also someone's TV. The procedure is [windows-machines.md](windows-machines.md#cpu-upgrade-to-the-ryzen-9-5900xt).

Decided 2026-11-10 (ask QfY0Fp), 12-13 slots: an MS-A2 9955HX barebone with razer-laptop's 96 GB SODIMM (5 slots), razer-laptop back to a 32 GB kit (G.Skill F5-5600S4040A16GX2 2x16 GB, $319 used, was the pick) for its 1 Unity slot, one DeskSlim X600 with a 9900X and 64 GB DDR5 (4 slots), and a Ryzen 9 5900XT for windows-media (3-4 slots). Ordered that day: the MS-A2 barebone, a Samsung 990 EVO Plus 2 TB for it, the 5900XT, and a Crucial E100 2 TB for windows-media; the DeskSlim parts and both RAM kits were still being chosen. Each CI disk needs a published high TBW: the E100 is rated 100 TBW at 2 TB and DRAM-less, so it goes on the light host, against 1,200 TBW for a 990 EVO Plus, SN850X or 990 Pro; QLC drives such as the fanxiang S501Q are out. Desktop DDR5 on eBay is often server RDIMM sold as "desktop": buy only a stick whose label ends -UA0/-UB0 (SK Hynix HMCG88MEBUA081N for 32 GB), never R or E. Bring-up is queue item `deskslim-ci-host` (board hc-231).

## 6-8 slots in one box

Researched 2026-11-03 for "6-8 more slots, a few thousand dollars". Prices are eBay and retailer asking prices from search results (eBay refused direct fetches), so check the Sold filter before buying. Power and single-thread figures are estimates from TDP and clocks. Single-thread is relative to a Zen 4 desktop part.

6-8 slots needs 24 or more physical cores and 128 GB. In 2026 the RAM decides the price: a 128 GB desktop DDR5 kit costs $2,087-2,800, a 32 GB DDR5 RDIMM $934-1,470, while a 16 GB DDR4 RDIMM was $60-90 (it has since roughly tripled; see [Buying in the RAM shortage](#buying-in-the-ram-shortage)). So buy a box that already has 128 GB in it. One big box also beats two small ones: each host is one Unity activation, and the pool is capped at 16 admitted runners for that reason (see [Unity license](#unity-license)).

| Option | All-in | Cores / slots | 1T vs Zen 4 | Load power | Notes |
|---|---|---|---|---|---|
| **Used Lenovo ThinkStation P620, Threadripper PRO 5975WX, 128 GB DDR4** | ~$2,100-2,600 | 32 / 8 | ~75% | ~350 W | **Recommended.** Prebuilt, RAM included, quiet tower. Add a 2 TB NVMe and a ~$50 GPU (no iGPU). CPU is locked to Lenovo boards, which only hurts resale. ~$460/yr power at $0.15/kWh, so it pays for itself in about a year against a $199/mo OVH RISE-5 |
| Used P620, Threadripper PRO 3975WX, 128 GB | ~$1,800 | 32 / 8 | ~60% | ~330 W | Zen 2. Only if a pinned PlayMode shard still passes its timing on it |
| DIY EPYC 7443 + Tyan S8030 or Gigabyte MZ32-AR0 eBay combo, 8x16 GB RDIMM | ~$1,900-2,100 | 24 / 7 | ~65% | ~280 W | Cheapest to own. BMC has VGA, no GPU needed. Quiet with a Noctua NH-U14S TR4-SP3. The board takes a 7543 or 75F3 later. Combo listings often misstate the CPU: confirm the exact part and that it is not vendor-locked |
| DIY EPYC 7543 + ASRock Rack ROMED8-2T or Supermicro H12SSL, 128 GB RDIMM | ~$2,300-2,700 | 32 / 8 | ~65% | ~300 W | RAM-bound at 128 GB; 256 GB makes it 10 |
| **Premium: EPYC 75F3 + Supermicro H12SSL + 256 GB (8x32 GB) RDIMM** | ~$4,500-5,000 | 32 / 10 | ~75% | ~350 W | Fastest Zen 3 cores on still-cheap DDR4, room past 8 slots, one activation |
| Mac Studio M5 Max, 128 GB | $5,099 new | 18 / 6 (RAM-bound) | fast | quiet | Native Unity, like the Mac Studio sold 2026-11-10. Only as its replacement |
| Used Dell Precision 7820, 16-24 core Xeon, 128 GB | $1,000-1,500 (2026-10-29) | 16-24 / 5-7 | ~50% | 250-350 W | Loud, slow cores |
| 2x Minisforum MS-A2 9955HX, 96 GB / 2 TB prebuilt | $2,159 each (sold out 2026-11-10) | 16 / 5 each | ~100% | ~75 W sustained | Two activations. Drops from 100 W to ~75 W in long loads (Notebookcheck), noisy fan. 128 GB is unofficial |

Ruled out:
- Strix Halo 128 GB minis (Framework Desktop, GMKtec EVO-X2, Beelink GTR9 Pro, HP Z2 Mini G1a): $3,400-4,350 each for 5 slots.
- Ryzen 9 9950X or 5950X: 16 cores is 5 slots; on 2026-11-10 prices they are the cheap path, see [Buying in the RAM shortage](#buying-in-the-ram-shortage).
- Threadripper 9960X: $6,400-8,600 for 7 slots, because the DDR5 RDIMM costs more than the CPU and board.
- Dell R7525 and other 2U servers: 50-70 dBA under load, closet or garage only.

Before ordering, settle the cap: the new box's slots plus the pool's registered slots must fit under `pool_max_admitted` (24 in `apps/ci-runner-governor/config.json`); if they do not, the new box replaces AWS slots, or the cap and the license question move. Then buy one box, run it at 4 slots, time a pinned PlayMode shard against razer-laptop, and ramp to 8.

## Laptops as runners

razer-laptop is in the pool. asus-laptop and hp-victus left it on 2026-11-03: as k3s nodes, Unity load there took the cluster down with the jobs (asus ran at 90C, hp-victus's 31G ran out). Laptops work but throttle under sustained load and their batteries swell when held at 100%. Set a 60-80% charge limit in the BIOS (or `thinkpad_acpi` thresholds on Linux) and run with the lid open.

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

### GitHub-hosted against the AWS burst hosts

Checked 2026-11-11: moving Unity jobs from the AWS hosts to GitHub-hosted runners costs more. Per slot-hour:

| Where | $ per slot-hour | Basis |
|---|---|---|
| Owned hosts | 0 at the margin | governor `rates` |
| aws-burst-2 (on-demand m7a.8xlarge, 6 slots) | ~0.31 | $1.855/h over 6 slots |
| aws-burst-1 (on-demand m7a.8xlarge, 5 slots) | ~0.37 | $1.855/h over 5 slots |
| aws-build-1 (on-demand m7a.8xlarge, 3 slots) | ~0.62 | $1.855/h over 3 slots |
| GitHub Linux 4-core, 16 GB | 0.72 | $0.012/min |
| GitHub Linux 8-core, 32 GB | 1.32 | $0.022/min |

- 4-core is the smallest that fits a slot (3 cores, 14 GB; a PlayMode shard peaks at 13.9 GB), and only just. The 2-core standard runner does not fit.
- On top of the rate, every hosted job imports a cold `Library/`, activates Unity again (see [Unity license](#unity-license)), and is billed in whole minutes. Jobs that need `lan` (Multi, player builds, git.kblab.me pushes) cannot run there at all.
- At the pool's 150-260 runner-hours a day (playground `docs/operations/ci-cost.md`, 2026-11-06), GitHub 4-core would cost about $3,200-5,600 a month, against the $200 `ci-burst-monthly` cap on AWS.
- Non-Unity jobs already use hosted runners (`LINUX_RUNNER_LIGHT`, else `ubuntu-latest`), which is where hosted runners make sense.
- To spend less, prefer Spot over on-demand and buy owned slots ([Options by value](#options-by-value)).

Source: docs.github.com/en/billing/reference/actions-runner-pricing (Linux x64), `apps/ci-runner-governor/config.json` `rates`.

A rented box joins like a home one: tailnet first (`docs/headscale-setup.md`, so it reaches `git.kblab.me`), then `unity_ci_runners` and `ci_hosts` in `ansible/inventory.yml`, the "Unity CI runner pool" play (`unity-ci-host` and `github-actions-runner-linux`), and the three rosters kept in step (`RUNNER_ROSTER` in `apps/github-runner-exporter/deployment.yaml`, the governor's `hosts`, the inventory; `scripts/check-runner-roster.py` fails CI on drift). Start it at about half its slots, prove it with one pinned PlayMode shard timed against hp-victus, then ramp. Add one host at a time, so the new activations do not all start together.

The pool already registers as many slots as `pool_max_admitted` allows (the numbers live in `ansible/inventory.yml` and `apps/ci-runner-governor/config.json`). A rented host adds parallel jobs only if the cap goes up, and the cap is there for the activation rate, so settle the license question first.

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
