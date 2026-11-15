# Tax-category scheme + business/personal separation (Actual Budget)

This is the scheme that makes the yearly export (see [tax-export.md](./tax-export.md)) tax-meaningful. It is set up once inside the Actual UI, then every transaction categorized into these groups flows into the CPA-handoff CSV already sorted by tax line.

Not tax advice. Category-to-line mappings are a starting point to confirm with a CPA or against IRS primary sources (Schedule C instructions, Pub 535). See the "tax-counsel" note at the bottom.

## Model: one budget file, separated by category group

Decision (2026-07): keep a SINGLE Actual budget file. SimpleFIN links per-file, so one file keeps bank sync simplest, and category groups separate business and personal spending. This stays clean to migrate to a dedicated tool (Invoice Ninja/Akaunting) later, because categories and accounts export independently.

Three levers, in order of importance:

1. Category group is the separator. The export groups by category, so the category a transaction lands in IS its tax classification. Put business spending in a Business category, personal in personal categories. Do not rely on notes/tags as the primary separator.
2. Account naming. Prefix business accounts so they read unambiguously in the CSV, e.g. `Biz - Deel Checking`, `Biz - Card`. Personal accounts keep plain names.
3. Notes convention (secondary). For a rare mixed transaction on a personal account, add `#biz` in the note so it is greppable, but prefer just categorizing it into the Business group.

## What exists in the file (2026-10-02)

The 2026 setup is a lean subset of the scheme below, sized to two C2C contracts plus the LLC. Add a line from the full tables only when a real expense needs it.

- Income: `W-2 Income (ADP)`, `Biz Income: OpenAI (Deel)` (Jan-Feb 2026, rule on `deel`), `Biz Income: Lazer` (People Center deposits, Apr-Aug 2026), `Other Income`.
- `Business - Schedule C`: `Biz: Software & subscriptions` (the old `Business` category, renamed), `Biz: Equipment & office`, `Biz: Legal & professional` (Incfile, Dun & Bradstreet, CPA), `Biz: Taxes & licenses` (CA LLC franchise tax), `Biz: Travel`, `Biz: Meals 50%`, `Biz: Phone & internet`.
- `Taxes & Deductions`: `Tax: Federal estimated`, `Tax: CA estimated`, `Ded: Charitable`. Medical stays in `Health`; it only counts above 7.5% of AGI.

Gross Lazer income is the full People Center deposit. The split rule routes 33% of each deposit to `Tax Reserve`, so the `Biz Income: Lazer` total understates it by a third. 360 Checking is off-budget and has no SimpleFIN history before late May 2026, so any Deel deposits or estimated-tax payments made from it before then are not in the file until a Capital One CSV is imported.

Itemizing: keep tracking deductions even if the standard deduction wins. With California tax on C2C income of this size, SALT alone may exceed the standard deduction (the SALT cap is $40k for 2025-2029). A CPA should confirm.

## Category groups to create in Actual

Create these groups under Budget -> Categories. Personal groups you already have; add the two tax-oriented groups.

### Group: "Business - Schedule C" (self-employment: Deel, consulting, BNB)

Each category maps to a Schedule C Part II expense line in this table. Keep the category names exact so reports and consumers can match them.

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
| Biz: AI | 27a (Other) | AI subscriptions and API charges; Agents lane |
| Biz: CI & build | 27a (Other) | CI runners and build services; CI lane |
| Biz: Hosting & infra | 27a (Other) | Hosting, domains, DNS and bank-sync service; Infra lane |
| Biz: Dues & memberships | 27a (Other) | Professional memberships |
| Biz: Bank/merchant fees | 27a (Other) | Stripe/PayPal/processor fees |
| Biz: Home office | Form 8829 | Tracked here; computed on Form 8829 |
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

## Dev spend migration (human-applied)

Status: instructions prepared; Actual categories, rules and 2026 history have not been verified or changed by this PR. Apply this migration at https://finance.kblab.me in the existing budget. Keep personal finances and bank transactions in Actual. Employer and client work accounts, including Gigantic and its AWS accounts, are outside this migration.

The three categories above replace `Biz: Software & subscriptions`. This table remains the tax-export category map: the [CSV exporter](../../apps/actual-budget/tax-export-cronjob.yaml) exports category names and has no separate hardcoded tax-line lookup. The split preserves the existing 27a mapping; CPA review of deductibility still applies.

### Vendor rules

Create one category rule per vendor/payee in Settings -> Rules. Match the exact payee already in Actual, and use the category below as its action. Registrar names and card descriptors must come from the UI; do not invent a payee or use a broad `Amazon` match. If a vendor has several payees, cover each observed payee. Retarget or remove the old category rules, including learned `payee is` rules that would outrank a new description rule.

| Vendor/payee to confirm in Actual | Category | Scope |
|---|---|---|
| Anthropic | Biz: AI | Claude subscriptions and API charges |
| OpenAI | Biz: AI | ChatGPT subscriptions and API charges; exclude Deel income |
| AWS | Biz: CI & build | Personal dev CI-burst charges; review mixed-use invoices before assigning |
| DigitalOcean | Biz: Hosting & infra | Hosting |
| GitHub | Biz: CI & build | Actions and build-related charges |
| Edgegap | Biz: Hosting & infra | Game-server hosting |
| Cloudflare | Biz: Hosting & infra | Paid DNS, domains and infrastructure |
| Each registrar shown in Actual | Biz: Hosting & infra | Business domain renewals; one rule per registrar/payee |
| SimpleFIN | Biz: Hosting & infra | Bank-sync service |

Review business use before adding a vendor-wide rule. A vendor that also bills personal purchases needs a narrower condition or manual categorization. Review remaining software vendors individually: assign each to the category matching its use, and resolve any expense that belongs elsewhere before retiring the old category. Refunds follow the original charge's category.

### Apply and verify once

1. Confirm a successful pre-change backup and its off-box NAS copy using the [backup procedure](./finance-stack.md#backups). Record the backup name and time locally. Do not proceed without a usable restore point.
2. Before changing anything, save a 2026 tax CSV using the [tax-export procedure](./tax-export.md). Keep it private and use a different filename from the later export. Record the signed total of all categories mapped to 27a in the table above, the old category's total, and a personal control category's total (for example Groceries). Confirm the CSV agrees with Actual's category report; an empty or unresolved-category export is a failed baseline.
3. Create `Biz: AI`, `Biz: CI & build` and `Biz: Hosting & infra` in `Business - Schedule C`. Keep the old category until all rows have been reviewed. Move any budgeted amounts and `#template` notes deliberately across the three categories, preserving their combined allocation; do not copy the old template into all three.
4. Apply the vendor rules above, replacing their old category actions. Keep the income, transfer, personal and Tax Reserve rules intact. Check for conflicting learned rules before categorizing history.
5. In Actual's transaction view, filter to 2026-01-01 through 2026-12-31 and the old category. Recategorize the existing transactions by payee using the table; inspect mixed purchases and split rows individually. Review remaining rows until the old category has zero transactions in the 2026 category report. Edit categories only, preserving dates, amounts, accounts, splits and transfer links. Leave every row categorized so To Budget does not move because of uncategorized spending.
6. Save a second 2026 tax CSV and compare it with the baseline. The signed 27a total must match exactly in cents, and the personal control total must be unchanged. The old category must have zero 2026 rows, and each migrated charge or refund must appear once in the intended category. Confirm account balances and the combined budget allocation are unchanged. Investigate any difference before treating the migration as complete.
7. After checking older history, future entries, category notes and rules, hide the empty old category. Do not delete or merge it in a way that assigns all history to one replacement category. Confirm one rule per vendor/payee sends a representative dev charge to the intended category, while OpenAI/Deel income and personal purchases retain their categories.

Record completion here without bank data: application date, backup reference, whether the old category has zero 2026 rows, whether the 27a comparison and personal control passed, and whether vendor rules were checked. Keep CSVs, payee descriptors and amounts private. Until that record exists, the Actual migration is pending even if the documentation PR has merged.

| Problem | Check and fix |
|---|---|
| New imports still use the old category | Retarget the exact-payee rule; it may outrank a description match. |
| The 27a total changes | Compare the two private exports by transaction; find omitted, duplicated or incorrectly moved rows before continuing. |
| To Budget changes unexpectedly | Find rows left uncategorized and review budget/template allocations; category edits must not alter balances. |

## Quarterly estimated taxes

Because Deel/consulting income has no withholding, self-employment usually owes quarterly estimated tax (Form 1040-ES). The `Biz: Income - *` categories keep gross receipts visible so you (or the tax-counsel agent over the export CSV) can size each quarter. The tax-export CronJob runs on the 1st of Jan/Apr/Jul/Oct - just before the 1040-ES due dates - by design.

## Migration path (later)

If business volume grows past what category-tagging in Actual can carry (client invoicing, time tracking, P&L, multi-currency), migrate the Business side to a dedicated tool - Invoice Ninja is already scoped in [finance-stack.md](./finance-stack.md). The separation model above makes that a clean lift: the `Biz:` categories and `Biz -` accounts are exactly the set that moves.

## tax-counsel agent (recommended, separate change)

Reasoning about which line a given expense belongs on, quarterly estimate sizing, and "is this deductible" questions want a dedicated `tax-counsel` agent (dotfiles repo, modeled on `compliance-counsel`: verify against IRS primary sources instead of memory, separate "what the data shows" from "what a CPA would rule," name the human-CPA gate). It would reason over the export CSV; it never files anything. Not built yet - surface and confirm before creating.
