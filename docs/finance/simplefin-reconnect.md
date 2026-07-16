# SimpleFIN bank-sync reconnect runbook

The budget file at finance.kblab.me went stale after 2026-04-13 (no transactions written since). SimpleFIN sync is either disconnected or its access token expired. This is the runbook to revive it. The re-auth steps are interactive in the Actual UI and in the SimpleFIN portal - they cannot be automated (they require your bank login), so this is a human task.

## 1. Confirm the app is reachable

Open https://finance.kblab.me and log in with the Actual server password. If it does not load, the app is down - check `kubectl --context home-k3s -n actual-budget get pods` first.

## 2. Check current link state

In Actual: Settings -> Linked Accounts (or the account's "..." menu -> Edit account -> Bank sync). If accounts show "not linked" or a sync error, the SimpleFIN token has lapsed.

## 3. Get a fresh SimpleFIN access URL

1. Log in at https://beta-bridge.simplefin.org/ (the SimpleFIN Bridge).
2. Confirm the subscription is active (~$1.50/mo) and your bank connections are still authorized - re-authorize any bank that dropped (banks force periodic re-login).
3. Create a new "Access URL" (a one-time setup token that Actual exchanges for a persistent access URL). Copy it.

## 4. Re-link in Actual

1. Actual -> Settings -> Linked Accounts -> Set up / Connect (SimpleFIN).
2. Paste the SimpleFIN setup token.
3. Map each SimpleFIN account to the matching Actual account (or create new). Keep the `Biz -` prefix on business accounts (see [tax-categories.md](./tax-categories.md)).
4. Trigger an initial sync.

## 5. Verify sync actually wrote

The tell that it worked is the budget SQLite mtime advancing past 2026-04-13:

```bash
kubectl --context home-k3s -n actual-budget exec deploy/actual-budget -- \
  sh -c 'ls -la --time-style=+%Y-%m-%d /data/user-files/*.sqlite'
```

A fresh mtime (today) plus new transactions appearing in the UI confirms sync is live again. If nothing imports, check the bank re-authorization in step 3 - an expired bank connection is the usual culprit.

## 6. Re-engage the budget

Sync only pulls transactions; they still need categorizing into the tax scheme. Set up the two tax category groups from [tax-categories.md](./tax-categories.md), then categorize the backlog since April. Going forward, Actual's rules (Settings -> Rules) can auto-categorize recurring payees.
