#!/usr/bin/env sh
# Childcare calculator — first-run setup.
set -e

cd "$(dirname "$0")"

echo "Checking layout..."
for f in Dockerfile docker-compose.yml server.js public/index.html; do
  [ -f "$f" ] || { echo "  MISSING: $f — unzip didn't preserve the structure"; exit 1; }
done
echo "  all four files present"

echo "Preparing data directory..."
mkdir -p data
# container runs as the node user, uid 1000
if [ "$(id -u)" = "0" ]; then
  chown -R 1000:1000 data
  echo "  data/ owned by uid 1000"
else
  CUR=$(stat -c %u data 2>/dev/null || echo "?")
  if [ "$CUR" != "1000" ]; then
    echo "  NOTE: data/ is owned by uid $CUR, container needs 1000."
    echo "        If saves fail, run: sudo chown -R 1000:1000 data"
  fi
fi

echo "Building and starting..."
docker compose up -d --build

echo "Waiting for health check..."
i=0
while [ $i -lt 30 ]; do
  if curl -fsS --max-time 2 http://127.0.0.1:8099/healthz >/dev/null 2>&1; then
    echo "  up"
    break
  fi
  i=$((i+1)); sleep 2
done

if [ $i -ge 30 ]; then
  echo "  did not come up — check: docker compose logs"
  exit 1
fi

echo "Testing persistence..."
curl -fsS -X PUT http://127.0.0.1:8099/api/state \
  -H 'Content-Type: application/json' -d '{"_selftest":1}' >/dev/null
if [ -f data/state.json ]; then
  echo "  state.json written"
  curl -fsS -X DELETE http://127.0.0.1:8099/api/state >/dev/null
else
  echo "  FAILED: data/state.json not created — run: sudo chown -R 1000:1000 data"
  exit 1
fi

echo ""
echo "Ready. http://127.0.0.1:8099"
echo "Next: point nginx at it using nginx.conf, then check the page's status"
echo "line reads 'Saved on the server'."
