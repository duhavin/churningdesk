import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";

const port = Number(process.env.WEWARDS_FRONTEND_PORT || 5177);
const host = process.env.WEWARDS_FRONTEND_HOST || "127.0.0.1";
const apiTarget = new URL(process.env.WEWARDS_API_TARGET || "http://127.0.0.1:8000");
const root = path.resolve(process.cwd(), "dist");

const mimeTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".ico", "image/x-icon"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

function send(res, status, body, headers = {}) {
  res.writeHead(status, headers);
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

async function proxyApi(req, res) {
  const body = await readBody(req);
  const target = new URL(req.url || "/", apiTarget);
  const headers = { ...req.headers, host: apiTarget.host };
  delete headers.connection;

  const proxyReq = http.request(
    target,
    {
      method: req.method,
      headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
      proxyRes.pipe(res);
    },
  );
  proxyReq.on("error", (error) => {
    send(
      res,
      502,
      JSON.stringify({ error: "backend_unavailable", detail: error.message }),
      { "content-type": "application/json; charset=utf-8" },
    );
  });
  if (body.length) proxyReq.write(body);
  proxyReq.end();
}

function safePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0] || "/");
  const clean = decoded === "/" ? "/index.html" : decoded;
  const candidate = path.resolve(root, `.${clean}`);
  return candidate.startsWith(root) ? candidate : path.join(root, "index.html");
}

async function serveStatic(req, res) {
  const filePath = safePath(req.url || "/");
  try {
    const stat = await fs.stat(filePath);
    if (!stat.isFile()) throw new Error("not a file");
    const body = await fs.readFile(filePath);
    send(res, 200, body, {
      "content-type": mimeTypes.get(path.extname(filePath).toLowerCase()) || "application/octet-stream",
      "cache-control": filePath.includes(`${path.sep}assets${path.sep}`)
        ? "public, max-age=31536000, immutable"
        : "no-store",
    });
  } catch {
    const body = await fs.readFile(path.join(root, "index.html"));
    send(res, 200, body, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
  }
}

const server = http.createServer((req, res) => {
  if ((req.url || "").startsWith("/api/")) {
    proxyApi(req, res).catch((error) => {
      send(
        res,
        500,
        JSON.stringify({ error: "proxy_failure", detail: error.message }),
        { "content-type": "application/json; charset=utf-8" },
      );
    });
    return;
  }
  serveStatic(req, res).catch((error) => {
    send(res, 500, `Static server error: ${error.message}`, { "content-type": "text/plain; charset=utf-8" });
  });
});

server.listen(port, host, () => {
  console.log(`WEwards frontend serving ${root} at http://${host}:${port}`);
  console.log(`Proxying /api to ${apiTarget.href}`);
});
