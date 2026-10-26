# Deploying to the home VM

How to get A Wee Bawbee running behind the existing nginx, signed in with local accounts. authentik is optional (PLAN.md decision 27).

## What you need

- A VM with Docker Engine and the Compose plugin (`docker compose version` works).
- nginx already terminating HTTPS for your domain, with a certificate for `bawbee.<my-domain>`.
- A checkout of this private repo on the VM. `seed/budget-seed.json` comes with it.

## 1. Configure

```sh
cd ~/a-wee-bawbee
cp .env.example .env
```

Edit `.env`:

- `BAWBEE_PUBLIC_URL=https://bawbee.<my-domain>`: the address you'll open on your phones.
- `BAWBEE_BIND` and `BAWBEE_PORT`:
  - If nginx runs on this same VM, leave them as `127.0.0.1` and `8000`.
  - If nginx runs on another VM, set `BAWBEE_BIND` to this VM's internal address and firewall the port to the nginx host only.
- Leave `BAWBEE_COOKIE_SECURE` unset. Session cookies are HTTPS-only, which is right behind nginx.

## 2. Start it

```sh
docker compose up -d --build
docker compose ps        # bawbee-app and bawbee-worker should both be "healthy"
curl -s http://127.0.0.1:8000/health
```

If it fails with **"Bind for …:8000 failed: port is already allocated"**, something else on the VM already uses port 8000:

1. Find it with `docker ps --filter publish=8000` or `sudo ss -ltnp 'sport = :8000'`.
2. Choose a free port, e.g. `BAWBEE_PORT=8010` in `.env`.
3. Run `docker compose up -d` again.
4. Use the same port in nginx's `proxy_pass`.

The app migrates the database on every start. The database lives in the `bawbee_bawbee-data` Docker volume, never in the repo.

## 3. Load the budget and create your logins

```sh
# The seed is read from stdin, so the real figures never get baked into the image.
docker compose exec -T bawbee-app bawbee import-seed - < seed/budget-seed.json

docker compose exec bawbee-app bawbee user create person_1 --person person_1 --name Person 1
docker compose exec bawbee-app bawbee user create person_2 --person person_2 --name Person 2
docker compose exec bawbee-app bawbee user list
```

Then the Plan screens' starting data, once (both are safe to run again; the second replaces the childcare setup):

```sh
# The Northfield mortgage and the past ones, linked to the budget's Mortgage line.
docker compose exec -T bawbee-app bawbee import-plan - < seed/budget-seed.json
# The old childcare calculator's saved state (or use Plan → Childcare → Bring the old setup in).
docker compose exec -T bawbee-app bawbee import-childcare - < reference/childcare-app/state.json
```

Then, in the app: the exact date the fix ends and a statement balance (Plan → Mortgage), the children's dates of birth (Plan → Childcare), each person's tax code and pension (Plan → Take-home), and the renewal dates (Plan → Renewals).

Each `user create` prompts twice for a password, which must be at least 12 characters. Person 2 can change theirs later in Settings. To reset a forgotten one:

```sh
docker compose exec bawbee-app bawbee user reset-password person_2
```

## 4. nginx

Copy `docs/nginx-bawbee.conf.sample` into your nginx config, set `server_name` and the certificate paths, and point `proxy_pass` at `BAWBEE_BIND:BAWBEE_PORT`. Then:

```sh
sudo nginx -t && sudo systemctl reload nginx
```

## 5. On your phones

1. Open `https://bawbee.<my-domain>` and sign in.
2. Add it to the home screen:
   - iPhone: Safari → Share → *Add to Home Screen*.
   - Android: Chrome → ⋮ → *Install app*.

   It opens full-screen as "Bawbee".

Phase 2 acceptance is both of you signing in on your phones and recreating this month's payday from the Payday screen.

## 6. Link your banks

Follow [providers.md](providers.md). In short:

1. Make the encryption key into `./secrets/fernet.key`, owned by uid 1000. Set `BAWBEE_FERNET_KEY_FILE`.
2. Put the Enable Banking `.pem` in `./secrets/` and set the two `BAWBEE_ENABLE_BANKING_*` lines. Whitelist `https://bawbee.<my-domain>/api/banks/callback` in its control panel. For Monzo, put the client secret in `./secrets/monzo_client_secret` and set the two `BAWBEE_MONZO_*` lines, with the same redirect URL on the Monzo client (docs/providers.md §3).
3. Run `docker compose up -d`.
4. On the Banks screen:
   - Person 1 links Monzo through Enable Banking.
   - Person 1 adds the Starling token, for Person 1 Starling and Joint Billing.
   - Person 2 signs in as themselves and links RBS.

The worker syncs at 06:00 and 18:00. After a week, `docker compose exec bawbee-app bawbee sync-report` is the Phase 4 check.

## Optional: sign in with authentik

Local passwords keep working alongside this, unless you set `BAWBEE_AUTH_LOCAL=false`.

1. In authentik, create an **OAuth2/OpenID Provider**:
   - Client type: *Confidential*.
   - Redirect URI: `https://bawbee.<my-domain>/api/auth/oidc/callback`.
   - Scopes: `openid email profile`.
   - Then create an **Application** that uses this provider.
2. Add to `.env`:
   ```sh
   BAWBEE_OIDC_ISSUER=https://auth.<my-domain>/application/o/<app-slug>/
   BAWBEE_OIDC_CLIENT_ID=...
   BAWBEE_OIDC_CLIENT_SECRET=...
   ```
   Then run `docker compose up -d` again.
3. Each of you signs in with your password, opens **Settings → Sign in with authentik → Link**, and approves at authentik. After that, the "Sign in with authentik" button logs into the same account.

An authentik login that isn't linked is refused; it never creates an account. If you created users with `--email` matching a verified authentik email, the first authentik sign-in links automatically.

## Updating

```sh
git pull
docker compose up -d --build     # migrations run on start
curl -s http://127.0.0.1:8000/api/version
```

`/api/version` names the commit the image was built from (read from `.git` during the build, so build from this git checkout) and the database's migration. It's public, like `/health`, and says nothing else. From a development checkout, `python scripts/check-deployed.py` compares it with the latest `main` and lists any commits the live app is missing.

## Backups

Nightly encrypted backups to the NAS are planned (PLAN.md §13). Until then:

- **Settings → Export → JSON backup** downloads every table, without login secrets.
- To copy the SQLite database safely while the app runs:
  ```sh
  docker compose exec bawbee-app python -c "import sqlite3; sqlite3.connect('/data/bawbee.db').backup(sqlite3.connect('/data/bawbee-backup.db'))"
  docker compose cp bawbee-app:/data/bawbee-backup.db ./bawbee-backup-$(date +%F).db
  ```
  Keep the copy somewhere private. It holds your real finances.
