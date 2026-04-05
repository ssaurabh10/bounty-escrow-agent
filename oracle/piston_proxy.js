/**
 * Piston Proxy Server — Bounty Escrow Agent
 * =========================================
 * Bridges the browser frontend to the Piston code execution API.
 * The public emkc.org instance requires server-side calls (no CORS for browsers).
 * This proxy runs locally on port 3458 (Judge0 proxy uses 3456 for this project).
 *
 * Usage:
 *   node oracle/piston_proxy.js
 *
 * Frontend calls: http://localhost:3458/execute
 * Proxy forwards to: https://emkc.org/api/v2/piston/execute
 *
 * Requirements: Node.js >= 18 (built-in fetch)
 */

const http  = require("http");
const https = require("https");

const PORT         = 3458;
const PISTON_HOST  = "emkc.org";
const PISTON_PATH  = "/api/v2/piston/execute";

// ── CORS headers ──────────────────────────────────────────────────────────────
const CORS_HEADERS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json",
};

// ── Server ────────────────────────────────────────────────────────────────────
const server = http.createServer((req, res) => {
  // Handle CORS preflight
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS_HEADERS);
    res.end();
    return;
  }

  // Health check
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, CORS_HEADERS);
    res.end(JSON.stringify({ status: "ok", proxy: "piston_proxy", port: PORT }));
    return;
  }

  // Only accept POST /execute
  if (req.method !== "POST" || req.url !== "/execute") {
    res.writeHead(404, CORS_HEADERS);
    res.end(JSON.stringify({ error: "Use POST /execute" }));
    return;
  }

  // Collect request body
  let body = "";
  req.on("data", chunk => { body += chunk.toString(); });
  req.on("end", () => {
    // Validate JSON
    let payload;
    try {
      payload = JSON.parse(body);
    } catch {
      res.writeHead(400, CORS_HEADERS);
      res.end(JSON.stringify({ error: "Invalid JSON body" }));
      return;
    }

    const bodyBuf = Buffer.from(JSON.stringify(payload));

    // Forward to Piston
    const options = {
      hostname: PISTON_HOST,
      path:     PISTON_PATH,
      method:   "POST",
      headers:  {
        "Content-Type":   "application/json",
        "Content-Length": bodyBuf.length,
        "User-Agent":     "BountyEscrowOracle/1.0",
      },
    };

    const pistonReq = https.request(options, pistonRes => {
      let data = "";
      pistonRes.on("data", chunk => { data += chunk; });
      pistonRes.on("end", () => {
        res.writeHead(pistonRes.statusCode, CORS_HEADERS);
        res.end(data);
        console.log(`[Proxy] ${new Date().toISOString()} | ${payload.language}@${payload.version} | HTTP ${pistonRes.statusCode}`);
      });
    });

    pistonReq.on("error", err => {
      console.error(`[Proxy] Piston request failed: ${err.message}`);
      res.writeHead(502, CORS_HEADERS);
      res.end(JSON.stringify({ error: "Piston unreachable: " + err.message }));
    });

    pistonReq.write(bodyBuf);
    pistonReq.end();
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log("╔══════════════════════════════════════════╗");
  console.log("║   Bounty Escrow — Piston Proxy Server    ║");
  console.log(`║   Listening on http://localhost:${PORT}   ║`);
  console.log("║   Forwarding → https://emkc.org/piston   ║");
  console.log("║   Press Ctrl+C to stop                   ║");
  console.log("╚══════════════════════════════════════════╝");
});
