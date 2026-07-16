# Tax-category scheme + business/personal separation (Actual Budget)

This is the scheme that makes the yearly export (see [tax-export.md](./tax-export.md)) tax-meaningful. It is set up once inside the Actual UI, then every transaction categorized into these groups flows into the CPA-handoff CSV already sorted by tax line.

Not tax advice. Category-to-line mappings are a starting point to confirm with a CPA or against IRS primary sources (Schedule C instructions, Pub 535). See the "tax-counsel" note at the bottom.

## Model: one budget file, separated by category group

Decision (2026-07): keep a SINGLE Actual budget file. SimpleFIN links per-file, so one file keeps bank sync simplest, and business vs personal separation is done by STRUCTURE, not by a second file. This stays clean to migrate to a dedicated tool (Invoice Ninja/Akaunting) later, because categories and accounts export independently.

Three levers, in order of importance:

1. Category group is the separator. The export groups by category, so the category a transaction lands in IS its tax classification. Put business spending in a Business category, personal in personal categories. Do not rely on notes/tags as the primary separator.
2. Account naming. Prefix business accounts so they read unambiguously in the CSV, e.g. `Biz - Deel Checking`, `Biz - Card`. Personal accounts keep plain names.
3. Notes convention (secondary). For a rare mixed transaction on a personal account, add `#biz` in the note so it is greppable, but prefer just categorizing it into the Business group.

## Category groups to create in Actual

Create these groups under Budget -> Categories. Personal groups you already have; add the two tax-oriented groups.

### Group: "Business - Schedule C" (self-employment: Deel, consulting, BNB)

Each category maps to a Schedule C Part II expense line. Name the Actual category with the line number so it self-documents.

| Actual category | Schedule C line | Notes |
|---|---|---|
| Biz: Advertising | 8 | Ads, promotion, domains-for-marketing |
| Biz: Car & truck | 9 | Mileage or actual; keep a mileage log separately |
| Biz: Contract labor | 11 | 1099 contractors you pay |
| Biz: Depreciation | 13 | Equipment > useful-life threshold (CPA handles) |
| Biz: Insurance | 15 | Business insurance (not health) |
| Biz: Legal & professional | 17 | Accountant, lawyer, this-CPA's fee |
| Biz: Office expense | 18 | Small office supplies, non-capital |
| Biz: Rent/lease | 20 | Coworking, equipment lease |
| Biz: Repairs | 21 | Equipment repair |
| Biz: Supplies | 22 | Consumables |
| Biz: Taxes & licenses | 23 | Business licenses, some fees |
| Biz: Travel | 24a | Airfare, lodging for business |
| Biz: Meals (50%) | 24b | Business meals; deductible portion is usually 50% |
| Biz: Utilities | 25 | Business-line phone/internet portion |
| Biz: Software & subscriptions | 27a (Other) | SaaS, dev tools, hosting - itemize under Other expenses |
| Biz: Dues & memberships | 27a (Other) | Professional memberships |
| Biz: Bank/merchant fees | 27a (Other) | Stripe/PayPal/processor fees |
| Biz: Home office | Form 8829 | Tracked here, but computed on Form 8829, not Schedule C directly |
| Biz: Income - Deel | Sch C line 1 | Gross receipts; keep income visible for quarterly estimates |
| Biz: Income - Consulting | Sch C line 1 | Other client income |

### Group: "Personal - Deductible" (only useful if you itemize on Schedule A)

Most filers take the standard deduction; populate this only if itemizing is likely.

| Actual category | Schedule A area |
|---|---|
| Ded: Medical | Medical & dental (over AGI floor) |
| Ded: State/local taxes | SALT (capped) |
| Ded: Mortgage interest | Home mortgage interest |
| Ded: Charitable - cash | Gifts to charity |
| Ded: Charitable - noncash | Gifts to charity (noncash) |

Everything else stays in your normal personal budgeting groups (Groceries, Rent, etc.) and is simply not tax-relevant.

## Quarterly estimated taxes

Because Deel/consulting income has no withholding, self-employment usually owes quarterly estimated tax (Form 1040-ES). The `Biz: Income - *` categories keep gross receipts visible so you (or the tax-counsel agent over the export CSV) can size each quarter. The tax-export CronJob runs on the 1st of Jan/Apr/Jul/Oct - just before the 1040-ES due dates - by design.

## Migration path (later)

If business volume grows past what category-tagging in Actual can carry (client invoicing, time tracking, P&L, multi-currency), migrate the Business side to a dedicated tool - Invoice Ninja is already scoped in [finance-stack.md](./finance-stack.md). The separation model above makes that a clean lift: the `Biz:` categories and `Biz -` accounts are exactly the set that moves.

## tax-counsel agent (recommended, separate change)

Reasoning about which line a given expense belongs on, quarterly estimate sizing, and "is this deductible" questions want a dedicated `tax-counsel` agent (dotfiles repo, modeled on `compliance-counsel`: verify against IRS primary sources instead of memory, separate "what the data shows" from "what a CPA would rule," name the human-CPA gate). It would reason over the export CSV; it never files anything. Not built yet - surface and confirm before creating.
