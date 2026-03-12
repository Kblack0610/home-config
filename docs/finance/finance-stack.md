# Self-Hosted Finance Stack

A comprehensive guide to self-hosted finance management for personal and business use.

## Overview

This document covers the self-hosted finance tools deployed in this home lab, recommendations for different use cases, and future expansion options.

## Current Setup

### Actual Budget (Personal Finance)

**Status:** ✅ Deployed
**URL:** https://finance.kblab.me
**Namespace:** `actual-budget`

Actual Budget is a local-first personal finance application focused on envelope budgeting.

#### What It Does Well

- **Bank Syncing**: Automated transaction imports via SimpleFIN (~$1.50/month)
- **Envelope Budgeting**: Allocate money to categories, track spending
- **Multi-device Sync**: Desktop and mobile apps sync to your server
- **Privacy**: All data stays on your server
- **Clean UI**: Modern, intuitive interface

#### What It's For

- Personal checking/savings accounts
- Credit card tracking
- Personal budgeting and spending analysis
- Net worth tracking

#### What It's NOT For

- Business invoicing
- Client billing
- Tax reporting
- Project-based expense tracking
- Time tracking

#### Setup Steps

1. Access https://finance.kblab.me
2. Create a new budget file
3. Set a server password
4. Configure SimpleFIN:
   - Sign up at https://simplefin.org/
   - Link your bank accounts
   - Copy access URL to Actual Budget → Settings → Linked Accounts

#### Maintenance

- **Backups**: Automated daily at 3 AM, 30-day retention
- **Location**: `/var/backups/actual-budget/` on k3s nodes
- **Manual backup**: `kubectl create job --from=cronjob/actual-budget-backup manual-backup -n actual-budget`

---

## C2C / Freelance Business Finance (Future)

For contractor/consulting work (like Deel income), Actual Budget is insufficient. Below are recommended self-hosted alternatives.

### Recommended: Invoice Ninja

**Best for:** Freelancers and consultants
**License:** Open Source (AAL for self-hosted)

#### Features

| Feature | Description |
|---------|-------------|
| Invoicing | Create, send, track professional invoices |
| Proposals | Send quotes that convert to invoices |
| Expenses | Track business expenses by category/client |
| Time Tracking | Built-in timer, billable hours |
| Projects | Organize work by client/project |
| Payments | Accept online payments (Stripe, PayPal) |
| Reports | Profit/loss, tax summaries, aging reports |
| Client Portal | Clients can view/pay invoices online |

#### Deployment (When Ready)

```yaml
# apps/invoice-ninja/deployment.yaml (example)
image: invoiceninja/invoiceninja:5
ports: 80
database: MySQL/MariaDB or PostgreSQL
```

#### Why Invoice Ninja for C2C

- Track Deel payments as income
- Create invoices for consulting clients
- Categorize expenses (software, equipment, home office)
- Generate reports for quarterly tax estimates
- Separate personal and business finances cleanly

---

### Alternative: Akaunting

**Best for:** Small business with traditional accounting needs
**License:** Open Source (GPLv3)

#### Features

- Double-entry accounting
- Invoicing and bills
- Multi-currency support
- Banking integration
- Financial reports (P&L, Balance Sheet)
- Inventory tracking
- App marketplace for extensions

#### When to Choose Akaunting Over Invoice Ninja

- Need formal accounting (double-entry)
- Multiple currencies (international clients)
- Want inventory/product tracking
- Prefer traditional accounting software feel

---

### Alternative: Crater

**Best for:** Simple invoicing with clean UI
**License:** Open Source (AGPLv3)

#### Features

- Invoice and estimate creation
- Expense tracking
- Payment recording
- Basic reporting
- Multi-language support

#### When to Choose Crater

- Want simplest possible setup
- Only need basic invoicing
- Prefer minimal UI

---

## Recommended Finance Stack

### For Your Situation (Deel + Consulting Income)

```
┌─────────────────────────────────────────────────────────┐
│                    PERSONAL FINANCE                      │
│                                                          │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Actual Budget                        │   │
│  │  • Bank accounts (checking, savings)             │   │
│  │  • Credit cards                                  │   │
│  │  • Personal budgeting                            │   │
│  │  • SimpleFIN bank sync                           │   │
│  └─────────────────────────────────────────────────┘   │
│                         ▲                               │
│                         │ Transfer to personal          │
│                         │                               │
├─────────────────────────┼───────────────────────────────┤
│                    BUSINESS FINANCE                      │
│                         │                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Invoice Ninja                        │   │
│  │  • Deel income tracking                          │   │
│  │  • Client invoices                               │   │
│  │  • Business expenses                             │   │
│  │  • Tax reports                                   │   │
│  │  • Time tracking                                 │   │
│  └─────────────────────────────────────────────────┘   │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### Why Separate Personal and Business?

1. **Tax Compliance**: Clear separation for deductions
2. **Audit Trail**: Business expenses documented properly
3. **Simplicity**: Each tool does one thing well
4. **Scalability**: Can add business partners, accountants later

---

## Comparison Matrix

| Feature | Actual Budget | Invoice Ninja | Akaunting | Crater |
|---------|--------------|---------------|-----------|--------|
| **Primary Use** | Personal | Freelance | Small Biz | Simple Invoice |
| **Bank Sync** | ✅ SimpleFIN | ❌ | ✅ Plaid | ❌ |
| **Invoicing** | ❌ | ✅ | ✅ | ✅ |
| **Expenses** | ✅ Basic | ✅ | ✅ | ✅ |
| **Time Tracking** | ❌ | ✅ | ❌ | ❌ |
| **Projects** | ❌ | ✅ | ❌ | ❌ |
| **Double-Entry** | ❌ | ❌ | ✅ | ❌ |
| **Reports** | Basic | Good | Excellent | Basic |
| **Multi-Currency** | ✅ | ✅ | ✅ | ✅ |
| **Mobile Apps** | ✅ | ✅ | ✅ | ❌ |
| **Complexity** | Low | Medium | High | Low |
| **Resource Usage** | Light | Medium | Medium | Light |

---

## Implementation Roadmap

### Phase 1: Personal Finance ✅ COMPLETE

- [x] Deploy Actual Budget
- [x] Configure ingress (finance.kblab.me)
- [x] Set up automated backups
- [ ] Configure SimpleFIN bank sync
- [ ] Import existing accounts
- [ ] Set up budget categories

### Phase 2: Business Finance (Future)

- [ ] Choose tool (Invoice Ninja recommended)
- [ ] Deploy to k3s cluster
- [ ] Configure domain (invoice.kblab.me or biz.kblab.me)
- [ ] Set up client database
- [ ] Import Deel payment history
- [ ] Configure expense categories for taxes

### Phase 3: Integration (Future)

- [ ] Document transfer workflow between business → personal
- [ ] Set up quarterly tax reminder automation
- [ ] Create backup strategy for both systems
- [ ] Optional: Connect to accounting software for tax prep

---

## Quick Reference

### Deployed Services

| Service | URL | Namespace | Purpose |
|---------|-----|-----------|---------|
| Actual Budget | finance.kblab.me | actual-budget | Personal finance |

### Useful Commands

```bash
# Check Actual Budget status
kubectl get all -n actual-budget

# View logs
kubectl logs -n actual-budget -l app=actual-budget

# Port forward (if ingress issues)
kubectl port-forward -n actual-budget svc/actual-budget 5006:80

# Manual backup
kubectl create job --from=cronjob/actual-budget-backup manual-backup-$(date +%s) -n actual-budget

# List backups
ls -la /var/backups/actual-budget/
```

### External Services

| Service | Purpose | Cost | URL |
|---------|---------|------|-----|
| SimpleFIN | Bank sync for Actual | ~$1.50/mo | https://simplefin.org |

---

## Resources

### Actual Budget
- Documentation: https://actualbudget.org/docs/
- GitHub: https://github.com/actualbudget/actual
- Community: https://discord.gg/actualbudget

### Invoice Ninja
- Documentation: https://invoiceninja.github.io/
- GitHub: https://github.com/invoiceninja/invoiceninja
- Demo: https://demo.invoiceninja.com

### Akaunting
- Documentation: https://akaunting.com/docs
- GitHub: https://github.com/akaunting/akaunting
- Demo: https://demo.akaunting.com

### Crater
- Documentation: https://docs.craterapp.com/
- GitHub: https://github.com/crater-invoice/crater

---

## Changelog

| Date | Change |
|------|--------|
| 2026-01-16 | Initial Actual Budget deployment |
| 2026-01-16 | Documentation created |
