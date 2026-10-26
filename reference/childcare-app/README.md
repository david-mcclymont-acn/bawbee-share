# Childcare calculator — self-hosted

Three ways to run it, in increasing order of effort.

## 1. No server at all

Open `public/index.html` in a browser. Everything works; nothing is saved
between visits unless you use **Download a copy** / **Restore from file**.

Browsers treat `file://` pages as having no origin, so browser storage is
blocked there. Serving it over http fixes that — which is option 2.

## 2. Static file on nginx — saves per browser

Drop `public/index.html` anywhere nginx already serves static files:

```
cp public/index.html /var/www/html/childcare/index.html
```

The page detects there's no `/api/state` endpoint and falls back to
`localStorage`. Added nurseries, patterns and salary all persist. The status
line next to the buttons reads **Saved in this browser**.

Caveat: storage is per browser and per device. Your phone and your laptop
keep separate copies, and clearing site data wipes it. For most uses that's
fine. If you want one shared setup, use option 3.

## 3. Container with server-side state — saves for everyone

```
docker compose up -d --build
```

Listens on `127.0.0.1:8099`. State is written to `./data/state.json` on an
atomic rename, so a crash mid-write can't corrupt it. Back up that one file
and you've backed up everything.

The page probes `api/state` on load; when it answers JSON the status line
reads **Saved on the server** and every change syncs, debounced at 600ms.
If the container is down the page still works, it just warns it can't save.

Point nginx at it with `nginx.conf` — there's a subdomain version and a
subpath version. The page uses a relative API path, so a subpath like
`/childcare/` works with no changes to the HTML.

### A note on access control

The state API has no authentication. It's a single shared document, not
per-user accounts — anyone who can reach the page can overwrite the saved
setup. That's fine on a LAN. The nginx config ships with an RFC1918
allowlist for that reason.

If you're exposing it through your Cloudflare tunnel, put authentik in front
of it — there's a forward-auth block at the bottom of `nginx.conf` ready to
uncomment. Not because the data is sensitive in itself, but a salary and a
tax code are more than you want indexed.

## Step by step

Assumes Docker and Docker Compose v2 on the host, nginx in front.

1. **Copy the zip over and unpack it.**

   ```
   scp childcare-app.zip person_1@server:/opt/
   ssh person_1@server
   cd /opt && unzip childcare-app.zip && cd childcare-app
   ```

2. **Run the setup script.**

   ```
   sh setup.sh
   ```

   (Zip doesn't carry the executable bit reliably. Run it with `sh` as above,
   or `chmod +x setup.sh` first if you prefer `./setup.sh`.)

   It checks the four required files are present, creates `data/` with the
   right ownership, builds, waits for the health check, then writes and
   deletes a test record to prove persistence works. If any step fails it
   says what to fix.

   To do it by hand instead: `mkdir -p data && sudo chown -R 1000:1000 data
   && docker compose up -d --build`.

3. **Check it's serving.**

   ```
   docker compose ps                   # healthy after ~30s
   curl -s localhost:8099/api/state    # {}
   ```

4. **Put nginx in front.**

   ```
   sudo cp nginx.conf /etc/nginx/sites-available/childcare
   sudo nano /etc/nginx/sites-available/childcare   # server_name, cert paths
   sudo ln -s /etc/nginx/sites-available/childcare /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```

   If nginx runs on a **different host** to Docker, change the compose ports
   line from `127.0.0.1:8099:8080` to `8099:8080` and restrict access with
   your firewall instead — the loopback bind is deliberate and will otherwise
   refuse the connection.

5. **Open it and check the status line** next to the buttons at the bottom.
   It should read **Saved on the server**. If it says *Saved in this browser*,
   nginx isn't passing `/api/state` through — usually a trailing slash in a
   subpath config.

   Add a nursery, reload, then open it on your phone. If the nursery is there
   too, server-side state is working.

## Updating

Replace `public/index.html`, then:

```
docker compose up -d --build
```

`data/state.json` is untouched by a rebuild.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `data/state.json` never appears | `data/` not owned by uid 1000 — `sudo chown -R 1000:1000 data` |
| Page loads, won't save | Check devtools Network for `PUT api/state`. 403/404 is nginx; connection error is the container |
| Health check stuck "starting" | `docker compose logs` |
| Port 8099 taken | Change the host side only: `127.0.0.1:8100:8080`, update `proxy_pass` |
| 404 on every page | `public/index.html` missing from the build context |

## Backups

One file holds everything:

```
/opt/childcare-app/data/state.json
```

The **Download a copy** button gives you the same JSON from the browser.
Worth doing once the nurseries are entered.

## Files

```
setup.sh             first-run: checks, builds, verifies persistence
server.js            zero-dependency Node server, static + state API
Dockerfile           node:22-alpine, runs unprivileged, /data volume
docker-compose.yml   loopback-bound, restart unless-stopped
nginx.conf           subdomain and subpath variants, optional authentik
.dockerignore        keeps data/ and docs out of the image
public/index.html    the calculator itself, self-contained
```

## API

```
GET    /api/state    returns the saved JSON, {} if nothing saved yet
PUT    /api/state    replaces it; rejects >256KB or malformed JSON
DELETE /api/state    clears it; the Reset button calls this
GET    /healthz      for the container healthcheck
```

Fonts load from Google Fonts. Without internet the page falls back to system
faces and is perfectly usable, just less pretty. If you'd rather it were
fully self-contained, download the four font files into `public/` and swap
the `<link>` for a local `@font-face` block.
