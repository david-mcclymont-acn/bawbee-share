// Childcare calculator - static server + state persistence. No dependencies.
const http = require("http");
const fs   = require("fs");
const path = require("path");

const PORT      = process.env.PORT || 8080;
const DATA_DIR  = process.env.DATA_DIR || "/data";
const PUBLIC    = path.join(__dirname, "public");
const STATE     = path.join(DATA_DIR, "state.json");
const MAX_BODY  = 256 * 1024;

fs.mkdirSync(DATA_DIR, { recursive: true });

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".ico":  "image/x-icon",
  ".css":  "text/css; charset=utf-8",
  ".js":   "text/javascript; charset=utf-8"
};

function send(res, code, body, type = "text/plain; charset=utf-8") {
  res.writeHead(code, {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff"
  });
  res.end(body);
}

function readState() {
  try { return JSON.parse(fs.readFileSync(STATE, "utf8")); }
  catch { return {}; }
}

function writeState(obj) {
  const tmp = STATE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2));
  fs.renameSync(tmp, STATE);           // atomic replace
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  const route = url.pathname.replace(/\/+$/, "") || "/";

  // ---- state API ----
  if (route === "/api/state") {
    if (req.method === "GET")
      return send(res, 200, JSON.stringify(readState()), TYPES[".json"]);

    if (req.method === "PUT") {
      let body = "", tooBig = false;
      req.on("data", c => {
        body += c;
        if (body.length > MAX_BODY) { tooBig = true; req.destroy(); }
      });
      req.on("end", () => {
        if (tooBig) return send(res, 413, "Payload too large");
        try {
          const parsed = JSON.parse(body);
          if (typeof parsed !== "object" || parsed === null) throw new Error("not an object");
          writeState(parsed);
          send(res, 200, JSON.stringify({ ok: true }), TYPES[".json"]);
        } catch {
          send(res, 400, JSON.stringify({ error: "invalid JSON" }), TYPES[".json"]);
        }
      });
      return;
    }

    if (req.method === "DELETE") {
      try { fs.unlinkSync(STATE); } catch {}
      return send(res, 200, JSON.stringify({ ok: true }), TYPES[".json"]);
    }

    res.setHeader("Allow", "GET, PUT, DELETE");
    return send(res, 405, "Method not allowed");
  }

  if (route === "/healthz") return send(res, 200, "ok");

  // ---- static files ----
  if (req.method !== "GET" && req.method !== "HEAD")
    return send(res, 405, "Method not allowed");

  const rel  = route === "/" ? "index.html" : route.slice(1);
  const file = path.join(PUBLIC, rel);
  if (!file.startsWith(PUBLIC + path.sep) && file !== path.join(PUBLIC, "index.html"))
    return send(res, 403, "Forbidden");

  fs.readFile(file, (err, buf) => {
    if (err) return send(res, 404, "Not found");
    send(res, 200, buf, TYPES[path.extname(file)] || "application/octet-stream");
  });
});

server.listen(PORT, () => console.log("childcare calculator on :" + PORT + ", data in " + DATA_DIR));
