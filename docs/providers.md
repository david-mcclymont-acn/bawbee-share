# Bank providers

A Wee Bawbee reads bank data four ways (PLAN.md §8.1). All of them are read-only: nothing in the app can move money.

| Accounts | Route | Who links it |
|---|---|---|
| Person 1 Monzo | Monzo Developer API (decision 29: Enable Banking doesn't offer Monzo) | Person 1 |
| Person 2 RBS | Enable Banking | Person 2, from their own login |
| Person 1 Starling, Joint Billing (Starling joint) | Starling personal access token | Person 1 (decision 21: the joint account is linked once) |
| Monzo Investment, or anything, any time | CSV import (Transactions) | Either of you |

Links are made on the **Banks** screen (More → Banks on a phone). Each link belongs to the person who made it. Both of you can see every link and press **Sync now**, but only the owner can re-link, replace a token or disconnect it. Linking and disconnecting ask for your password again if you signed in more than 12 hours ago.

## 1. The encryption key (needed for any bank link)

Bank session ids, Monzo's tokens and the Starling token are encrypted in the database with a Fernet key. Make one on the VM and keep it in `./secrets`, which is gitignored and mounted read-only into both containers:

```sh
mkdir -p secrets
docker compose run --rm bawbee-app bawbee fernet-key > secrets/fernet.key
chmod 600 secrets/fernet.key && sudo chown 1000:1000 secrets/fernet.key   # the container user
```

Set `BAWBEE_FERNET_KEY_FILE=/run/bawbee-secrets/fernet.key` in `.env`. Back the key up somewhere safe that isn't next to the database backups. If it's lost, re-link every bank. Nothing else is lost.

## 2. Enable Banking (RBS)

1. Sign up at [enablebanking.com](https://enablebanking.com) and create an application.
   - Start in the **sandbox** environment to try the flow, then register a **production** application. Restricted production mode is free for linking your own accounts, non-commercially.
   - Let it generate the private key. Save the `.pem` as `secrets/enable_banking.pem` (`chmod 600`, owned by uid 1000).
   - Whitelist the redirect URL `https://bawbee.<your-domain>/api/banks/callback`. It must be the address in `BAWBEE_PUBLIC_URL`. The bank redirects the browser there, so it doesn't need to be reachable from the internet.
2. In `.env`:
   ```
   BAWBEE_ENABLE_BANKING_APP_ID=<the application id>
   BAWBEE_ENABLE_BANKING_KEY_FILE=/run/bawbee-secrets/enable_banking.pem
   ```
3. `docker compose up -d`, then on the Banks screen choose **Link a bank**, pick RBS (Royal Bank of Scotland), sign in at the bank, and choose which of our accounts each bank account feeds.
4. In restricted mode, only linked accounts are visible to the application. Person 2 links RBS from their own login so the consent is theirs.

**Check before relying on it** (PLAN.md §8.1):
- RBS personal accounts are listed under GB in the Enable Banking control panel.
- How long the bank lets a consent last. The app asks for the bank's maximum, up to 180 days, and shows the end date on the Banks screen.
- Person 2 linking their own accounts fits the restricted-mode terms.

## 3. Monzo Developer API (Person 1 Monzo)

Enable Banking doesn't list Monzo, so Monzo is read through [Monzo's own API](https://docs.monzo.com/). Monzo allows it for **personal use only**: the client below is Person 1's, for this household's app, and nobody else's account.

1. Sign in at [developers.monzo.com](https://developers.monzo.com) with Person 1's Monzo (it sends an email link, then asks for approval in the Monzo app) and choose **Clients → New OAuth Client**:
   - **Name:** A Wee Bawbee. **Logo:** none needed.
   - **Redirect URL:** `https://bawbee.<your-domain>/api/banks/callback`, the same address as `BAWBEE_PUBLIC_URL`. It needn't be reachable from the internet; Monzo only sends the browser there.
   - **Confidentiality:** **Confidential**. Only confidential clients get a refresh token; without one the link ends every six hours.
2. Save the client secret as `secrets/monzo_client_secret` (`chmod 600`, owned by uid 1000), and in `.env`:
   ```
   BAWBEE_MONZO_CLIENT_ID=oauth2client_...
   BAWBEE_MONZO_CLIENT_SECRET_FILE=/run/bawbee-secrets/monzo_client_secret
   ```
3. `docker compose up -d`. On the Banks screen choose **Link a bank → Monzo** and sign in (Monzo emails a magic link: open it on the same device).
4. **Approve it in the Monzo app.** Monzo shows a request to allow access; until it's allowed nothing can be read, and the Banks screen says *Waiting for you to allow access in the Monzo app*. Allow it, then press **Continue**.
5. Map the personal account to Person 1 Monzo and **save straight away**: Monzo shares the full history only in the first **five minutes** after approval, and the last 90 days after that. The first sync runs on save. If the window is missed, the CSV export from the Monzo app fills the gap (the bank takes over from the day after the last CSV row).

How the Monzo link behaves:
- Access tokens last about six hours and each refresh replaces both tokens; the new pair is saved, encrypted, at once. There's no consent end date, so no 14-day reminder. If Monzo ends the sign-in (or a refresh fails), the link shows as ended and **Re-link** goes through steps 3 and 4 again, keeping the same accounts.
- Moves into and out of pots come through labelled **Pot: \<name\>** and are never proposed as regular bills. Declined card payments are skipped. Pending card payments appear as pending and settle in a later sync.
- The joint Monzo account (if there is one) is offered too; leave it as *Don't bring it in* unless it's one of ours.
- It's read-only: the app only ever makes `GET` calls for data (plus the token exchange and the logout on Disconnect). Monzo's pot deposit and withdraw endpoints are never called.
- Monzo Investment isn't in Monzo's API. It stays on CSV import, or a manual balance.

## 4. Starling personal access token (Person 1 Starling and Joint Billing)

1. In the [Starling developer portal](https://developer.starlingbank.com), sign in with Person 1's Starling login and create a **personal access token** with read scopes only: `account:read`, `account-identifier:read`, `balance:read`, `transaction:read`, `space:read`. Never tick a payment scope.
2. On the Banks screen choose **Add a Starling token**, paste it, and map the accounts: the personal one to Person 1 Starling and the joint one to Joint Billing.
3. Check that the joint account appears. If the token can't see it, link the joint account through Enable Banking instead. Link it once only, under Person 1.

The token doesn't expire, but revoking it in the portal stops syncs. The Banks screen then says so, and **Replace token** takes a new one.

**Spaces.** Starling keeps a separate feed for each Space. Bills Manager is a spending Space that pays Direct Debits and standing orders itself, so those payments are only in its feed. Each sync reads the main feed and every spending Space's feed into the same account. Money moving between the main balance and a Space is labelled as our own (`pot_transfer`, as with Monzo pots): it's never a regular payment or something to look at, and both sides are kept, so the account still adds up. Without `space:read` the main feed still syncs, but bills paid from a Space are missed.

**Fetching again.** A sync only re-fetches the last week. After a change like this one, `bawbee sync --full` re-fetches each account from the day the bank took over, prints that day for each account, then runs the alerts, so missed payments that turn up clear themselves.

## How syncing works

- The worker syncs every active link at **06:00 and 18:00** UK time (`BAWBEE_SYNC_HOURS`). Banks allow only a few unattended syncs a day. **Sync now** is allowed once every 30 minutes per link.
- **CSV hand-over.** When an account that already has CSV imports is first mapped, the bank takes over from the day after its last CSV row. CSV rows for days the bank has covered are skipped from then on, so nothing is counted twice. CSV stays the fallback if a consent lapses.
- Each sync re-fetches the last week, so a pending payment that books (sometimes under a new id, sometimes at a different amount) replaces its pending row instead of adding another.
- Transfers between our accounts tick the payday checklist themselves. Price changes go to the Inbox as before.
- **Consents.** At 07:00 the worker raises an Inbox item 14 days before a consent ends, addressed to the link's owner (Enable Banking only: Monzo and Starling have no end date). If a bank refuses a consent early, the link is marked ended and the same Inbox item appears.
- **Disconnect** ends the session at the bank (Enable Banking's session, Monzo's sign-in) and here, and optionally deletes the transactions it brought in.

## Commands

```sh
docker compose exec bawbee-app bawbee sync                 # sync every link now
docker compose exec bawbee-app bawbee sync --connection 3  # one link
docker compose exec bawbee-app bawbee consents             # raise consent reminders now
docker compose exec bawbee-app bawbee sync-report --days 7 # syncs per link, and anything doubled
docker compose exec bawbee-app bawbee providers            # which bank links work here, and why not
```

**A bank isn't in the Link a bank list?** The list holds only what this server is set up for, so run `bawbee providers`. It says, for each route, what's missing: a variable not set, a secret file that isn't mounted, isn't readable by the container (uid 1000) or is empty. For Enable Banking it also says how many GB banks it lists and whether RBS is one. The Link a bank sheet shows the same reason when a search finds nothing. After changing `.env` or `./secrets`, recreate the containers (`docker compose up -d`). After pulling new code, rebuild them too (`docker compose up -d --build`).

`sync-report` is the Phase 4 acceptance check. After a week it should show about 14 good syncs per link and `CSV rows on bank-covered days: 0`. Any look-alike rows it lists are for a person to check, because two identical coffees on one day are real.

## Codespaces and CI

Real banks and real data stay on the home VM, and the Monzo client is never set up outside it. Elsewhere, set `BAWBEE_BANKS_DEMO=true` and `BAWBEE_FERNET_KEY` to a throwaway key (`bawbee fernet-key`). That gives a **Demo Bank** with made-up transactions that goes through the real link, sync and consent code. CI's e2e job uses it.
