# SimpleFIN bank-sync troubleshooting runbook

Bank sync into the budget at finance.kblab.me stopped writing transactions after 2026-04-13.

The obvious reading of that is "the SimpleFIN token expired". On 2026-08-10 that reading was checked against the live bridge and it was wrong: the token was valid, the subscription was active, and the bridge was serving current data the whole time. Reconnecting would have cost a bank-login session and fixed nothing.

So: diagnose first, reconnect only if the diagnosis says the credential is actually dead.

## 1. Run the diagnostic

`apps/actual-budget/tools/simplefin-diagnose.js` compares what the bridge is serving against what the budget file is linked to. It is read-only and never prints the access key.

```bash
kubectl --context home-k3s -n actual-budget exec -i deploy/actual-budget -- \
  sh -c 'cat > /tmp/d.js && node /tmp/d.js; rm -f /tmp/d.js /tmp/budget.sqlite' \
  < apps/actual-budget/tools/simplefin-diagnose.js
```

Read the result as one of three outcomes:

| Outcome | Meaning | Go to |
|---|---|---|
| `bridge HTTP` is not 200 | The access key really is dead or the subscription lapsed | Section 3 |
| `MATCH` but `last_sync` is stale or `NEVER` | The link is intact and data is waiting. The failure is client-side | Section 2 |
| `ORPHAN` | The bridge no longer serves that account id at all | Section 4 |

An account is `MATCH` or `ORPHAN` purely on whether its stored SimpleFIN account id is among the ids the bridge returns today. The id is what the linkage is keyed on, not the account name and not the last four digits.

## 2. MATCH but not importing: the client-side traps

**The Actual version.** Before 26.4.0 a single missing account aborted the entire SimpleFIN batch, so one de-listed bank silently blocked sync for every other account too. The fixes are 26.4.0 #7125 (isolate errors per account) and #7152 (handle missing accounts). Later versions add more: 26.7.0 #8113 fixes sync hanging indefinitely, #8017 makes a failed sync stay flagged instead of clearing on reload, and #8170 surfaces the error banner even when the failure happened on another device. If the deployed image predates these, upgrade before debugging anything else, because on an old image a partly broken link presents as a totally silent one.

**Nothing triggered a sync.** Actual pulls bank data from the client, not on a server schedule. `last_sync` records the last time a client asked. If it reads 2026-04-13, that is the last time sync ran, which is not the same as the last time it worked.

The check is cheap: open finance.kblab.me and sync, then re-run the diagnostic and confirm `last_sync` advanced.

## 3. The access key really is dead

Only do this when the diagnostic returns a non-200. The re-auth is interactive in the Actual UI and in the SimpleFIN portal, and it needs your bank login, so it cannot be automated.

1. Log in at https://beta-bridge.simplefin.org/ (the SimpleFIN Bridge) and confirm the subscription is active (about $1.50/mo).
2. Re-authorize any bank that dropped. Banks force periodic re-login.
3. Create a new Access URL (a one-time setup token that Actual exchanges for a persistent access URL) and copy it.
4. In Actual: Settings -> Bank Sync -> set up SimpleFIN, paste the setup token.
5. Re-link each account. See section 4, because the ids will have changed. Keep the `Biz -` prefix on business accounts (see [tax-categories.md](./tax-categories.md)).

Note that resetting the token has historically not reset the stored access key (fixed in 26.7.0 #8068). On an older image, verify the key actually changed rather than assuming it did.

## 4. ORPHAN accounts: re-authorize, then RE-LINK

An orphan means the bridge is not serving that account id any more, usually because the bank dropped out of SimpleFIN or the account was re-enrolled. As of 2026-08-10 all four Capital One accounts were orphans while U.S. Bank and American Express kept working.

Re-authorizing the bank at the bridge is only half the fix. When the account comes back it comes back with a **new** SimpleFIN account id, which will not match the id the budget account is still pointing at. You have to re-link it in Actual (the account's "..." menu -> Edit account -> Bank sync) so it binds to the new id. Skipping this leaves the account looking linked while importing nothing, which is the exact state that made this look like a token problem.

A name or last-four match is not evidence of a working link. American Express is the counter-example: the budget calls it `Amex - Credit (3007)` and the bridge calls it `Platinum Card (4005)`, a reissued card number, but the underlying id is unchanged so sync works fine.

## 5. Verify

Re-run the diagnostic. Success is `last_sync` advancing to today for every `MATCH`, and no remaining `ORPHAN` rows for banks you re-authorized.

The budget SQLite mtime is a weaker signal and can mislead: it advances on any client sync, including one that imported nothing.

```bash
kubectl --context home-k3s -n actual-budget exec deploy/actual-budget -- \
  sh -c 'ls -la --time-style=+%Y-%m-%d /data/user-files/'
```

## 6. Re-engage the budget

Sync only pulls transactions, it does not categorize them. Set up the two tax category groups from [tax-categories.md](./tax-categories.md), then categorize the backlog since April. Going forward, Actual's rules (Settings -> Rules) can auto-categorize recurring payees.
