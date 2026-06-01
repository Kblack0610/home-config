# Cal.com setup runbook

End-to-end bootstrap for [`apps/cal/`](../../apps/cal/). Self-hosted booking
page on `cal.kennethblack.me`, backed by the shared Postgres in `databases`
namespace, sending booking-confirmation email via Gmail SMTP.

This is the runbook for the first install. For day-to-day image bumps see
[`apps/cal/README.md`](../../apps/cal/README.md#image-bumps).

## Prerequisites

- `home-k3s` kubectl context configured + working
- `sops` + the age key on this machine (`~/.config/sops/age/keys.txt` or
  `SOPS_AGE_KEY_FILE` set)
- `cloudflare-ops` skill set up so DNS records can be added via the API
- An iPhone, a desktop browser, or whatever you'll use to verify the booking
  page end-to-end at the end

## 1. Provision the database

The shared Postgres in `databases` namespace already runs (mem0 lives on it).
We add a `cal` role and `cal` database.

```bash
# Generate the password and remember it — we'll stamp the same value into
# two places: apps/postgres/secret.yaml (so the init script can recreate
# the role on a fresh boot) and apps/cal/secret.yaml (so cal can connect).
POSTGRES_CAL_PASSWORD=$(openssl rand -base64 32)
echo "$POSTGRES_CAL_PASSWORD"   # save this somewhere short-lived

# Run the equivalent SQL against the live Postgres (the init script in
# apps/postgres/init-configmap.yaml only runs on a fresh data dir).
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -c "CREATE ROLE cal LOGIN PASSWORD '$POSTGRES_CAL_PASSWORD';"
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -c "CREATE DATABASE cal OWNER cal;"
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql -U postgres -d cal -c "GRANT ALL PRIVILEGES ON SCHEMA public TO cal;"

# Smoke-test the cal role can connect:
kubectl --context home-k3s -n databases exec -it deploy/postgres -- \
  psql "postgresql://cal:$POSTGRES_CAL_PASSWORD@localhost:5432/cal" -c "SELECT 1"
```

Then add `POSTGRES_CAL_PASSWORD` to the postgres secret so a future fresh
install can reproduce this state:

```bash
sops apps/postgres/secret.yaml
# Add line under stringData:
#   POSTGRES_CAL_PASSWORD: <the same value you generated above>
```

## 2. Generate cal.com secrets

```bash
# Cal.com session encryption — random 32 bytes
NEXTAUTH_SECRET=$(openssl rand -base64 32)

# Encrypts integration credentials (Google OAuth tokens, etc.) at rest in
# the cal Postgres database. Never rotate without re-encrypting the
# relevant rows; cal.com has no built-in key-rotation flow.
CALENDSO_ENCRYPTION_KEY=$(openssl rand -base64 32)

echo "NEXTAUTH_SECRET=$NEXTAUTH_SECRET"
echo "CALENDSO_ENCRYPTION_KEY=$CALENDSO_ENCRYPTION_KEY"
```

## 3. Get a Gmail SMTP app password

1. Pick one of the 5 Gmails to be the relay account. The booking-confirmation
   "From" address is set in `configmap.yaml` (`EMAIL_FROM=cal@kennethblack.me`),
   but Gmail will rewrite the envelope sender to the actual Gmail account to
   keep DMARC happy.
2. Make sure 2FA is enabled on that Google account (required for app passwords).
3. Go to <https://myaccount.google.com/apppasswords>, create a new password
   labelled `cal.kennethblack.me`. You get a 16-character string. Drop the
   spaces — Gmail prints it grouped, but it's used as one continuous string.

## 4. Fill `apps/cal/secret.yaml`

```bash
cp apps/cal/secret.yaml.template apps/cal/secret.yaml
$EDITOR apps/cal/secret.yaml
```

Fill in `REPLACE_WITH_*` placeholders with the values from steps 1–3, then:

```bash
sops --encrypt --in-place apps/cal/secret.yaml
git diff apps/cal/secret.yaml   # confirm only stringData fields are encrypted
```

## 5. Add DNS

Use the `cloudflare-ops` skill to add a record for `cal.kennethblack.me`.
Match whatever the existing portfolio app uses (likely an A record pointing
at the home-k3s ingress public IP, or a CNAME to a Cloudflare-tunnel-style
alias). Copy from `apps/portfolio/`'s deployed pattern.

```bash
# Sanity check after adding:
dig +short cal.kennethblack.me
```

## 6. Activate

```bash
# Add cal to the apps roll-up
$EDITOR apps/kustomization.yaml      # add `- cal` alphabetically after `bnb-studios`
                                     # (or wherever fits the existing order)

git add apps/cal/ apps/kustomization.yaml apps/postgres/init-configmap.yaml \
        apps/postgres/secret.yaml docs/runbooks/cal-com-setup.md
git commit -m "feat(cal): self-hosted booking page on cal.kennethblack.me"
git push

# Watch Flux reconcile
flux --context home-k3s reconcile kustomization apps --with-source
kubectl --context home-k3s -n cal get pods -w
```

First pod start takes 60–120s — the cal.com entrypoint runs
`prisma migrate deploy` against an empty `cal` database, which creates
~80 tables. Watch logs:

```bash
kubectl --context home-k3s -n cal logs -f deploy/cal-web
```

## 7. Verify

```bash
# Cert issued by cert-manager via Cloudflare DNS-01 (takes 1–2 min)
kubectl --context home-k3s -n cal get certificate cal-tls
# Should be: Ready=True

# Public URL responds
curl -sI https://cal.kennethblack.me | head -3
# Expect: HTTP/2 307 (redirect to /auth/signin) or 200

# Open the URL in a browser, create the first user account.
# That account becomes the admin.
```

## 8. Connect Google calendars + create the booking page

Inside the Cal.com UI:

1. Settings → My account → set timezone, name, etc.
2. Apps → Calendars → Google Calendar → **Install** → OAuth flow → grant
   read+write to one Gmail's calendar.
3. **Repeat the OAuth flow for each of the other 4 Gmails.** Cal.com lets
   you connect multiple Google accounts to one user; busy times merge across
   all of them.
4. In the Calendars settings, pick **one** calendar as the booking
   destination (where new bookings get written). The other 4 stay as
   busy-time sources only.
5. Event Types → New → "30 min meeting" or whatever shape you want.
6. The public link is something like `https://cal.kennethblack.me/kenneth/30min`.

## 9. Configure Google OAuth credentials (only if Cal.com prompts)

Cal.com's Google Calendar app uses Cal.com's *own* OAuth client by default.
If for any reason you want your own (rate limit, vanity, security), set
`GOOGLE_API_CREDENTIALS` in the secret to a JSON blob from Google Cloud
Console. Otherwise leave it unset — the default works for personal use.

If you do set up a custom OAuth client:

1. Google Cloud Console → New project `cal-kennethblack` → enable
   **Google Calendar API**.
2. OAuth consent screen → External → add your 5 Gmails as Test Users (or
   submit for verification if comfortable).
3. Credentials → Create OAuth 2.0 Client (Web app) →
   Authorized redirect URI: `https://cal.kennethblack.me/api/integrations/googlecalendar/callback`
4. Download the JSON, paste it into `apps/cal/secret.yaml`'s
   `GOOGLE_API_CREDENTIALS` field as a single line, re-encrypt.
5. Restart the cal pod: `kubectl --context home-k3s -n cal rollout restart deploy/cal-web`

## End-to-end booking smoke test

1. Open the public booking link in a browser **incognito window** (acts as a
   booker).
2. Confirm available timeslots reflect Google busy times — pick a Gmail you
   connected, drop a calendar block in there for an upcoming hour, refresh
   the booking page, the slot should be gone.
3. Book a test slot as the incognito user with a real email address.
4. Confirm:
   - The booker received a confirmation email (proves Gmail SMTP works).
   - The event appears on the destination Google calendar (proves OAuth
     write access works).
   - The slot is now blocked on subsequent loads of the booking page.

If the booker email never arrives, check `kubectl logs deploy/cal-web` for
SMTP errors. Most common: Gmail rejecting the auth (wrong app password) or
the From address (`cal@kennethblack.me` — make sure
[Send Mail As](https://support.google.com/mail/answer/22370) is configured
on the Gmail relay account if you want messages to actually appear from
that From, otherwise Gmail will rewrite to the relay account's address).

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Pod CrashLoopBackOff with `database "cal" does not exist` | Skipped step 1 |
| Pod CrashLoopBackOff with `password authentication failed` | Mismatched `POSTGRES_CAL_PASSWORD` between Postgres and cal.secret |
| Pod up but `https://cal.kennethblack.me` returns 502 | DNS not propagated yet, or ingress not seeing the service |
| Cert stuck on `Issuing` | Check `kubectl -n cert-manager logs deploy/cert-manager`; usually a Cloudflare API token issue |
| `NEXTAUTH_URL` warnings in logs | Mismatched URL in configmap — must equal the ingress host exactly, with `https://` |
| Booking page loads but Google OAuth fails | Default Cal.com OAuth client is rate-limited; set up a custom one (step 9) |
| Email never sends | Gmail app password wrong (regenerate at apppasswords), or 2FA not enabled on the relay account |
