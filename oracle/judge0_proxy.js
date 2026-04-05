/**
 * Judge0 Proxy Server — Bounty Escrow Agent
 * ========================================
 * Bridges the browser frontend to Judge0 CE (online judge).
 *
 * Why:
 * - Browser can't safely call many public judge endpoints (CORS, throttling).
 * - This proxy runs locally, adds CORS, and normalizes Judge0 output into a
 *   Piston-like response shape:
 *     { run: { stdout, stderr, code } }
 *
 * Usage:
 *   node oracle/judge0_proxy.js
 *
 * Frontend calls:
 *   http://localhost:3456/execute
 *   http://localhost:3456/health
 *
 * Config (optional env vars):
 *   JUDGE0_BASE_URL   (default: https://ce.judge0.com)
 *
 * Notes:
 * - Uses synchronous submissions (wait=true) for simplicity.
 * - Judge0 language is chosen using a minimal map; extend as needed.
 */
const http = require("http");
const { URL } = require("url");

const PORT = 3456;
const BASE_URL = (process.env.JUDGE0_BASE_URL || "https://ce.judge0.com").replace(/\/+$/, "");

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json",
};

function json(res, statusCode, obj) {
  res.writeHead(statusCode, CORS_HEADERS);
  res.end(JSON.stringify(obj));
}

function collectJson(req, res, cb) {
  let body = "";
  req.on("data", (chunk) => (body += chunk.toString()));
  req.on("end", () => {
    try {
      cb(JSON.parse(body || "{}"));
    } catch {
      json(res, 400, { error: "Invalid JSON body" });
    }
  });
}

function decodeB64Maybe(s) {
  if (!s) return "";
  // We call Judge0 with base64_encoded=false, but some deployments may still encode.
  // If it looks like base64, try decode; otherwise return as-is.
  try {
    if (/^[A-Za-z0-9+/=\r\n]+$/.test(s) && s.length % 4 === 0) {
      const buf = Buffer.from(s, "base64");
      const text = buf.toString("utf8");
      // Heuristic: decoded text should be mostly printable.
      if (text && /[^\x09\x0A\x0D\x20-\x7E]/.test(text) === false) return text;
    }
  } catch {}
  return s;
}

// Minimal Judge0 language_id mapping.
// Extend when you need more languages.
const LANGUAGE_ID = {
  python: 71,       // Python (3.x)
  c: 50,            // C (GCC)
  cpp: 54,          // C++ (GCC)
  javascript: 63,   // JavaScript (Node.js)
  typescript: 74,   // TypeScript
  go: 60,           // Go
  rust: 73,         // Rust
};

function guessLanguageFromFilename(name) {
  const n = (name || "").toLowerCase();
  if (n.endsWith(".py")) return "python";
  if (n.endsWith(".c")) return "c";
  if (n.endsWith(".cpp") || n.endsWith(".cc") || n.endsWith(".cxx")) return "cpp";
  if (n.endsWith(".js")) return "javascript";
  if (n.endsWith(".ts")) return "typescript";
  if (n.endsWith(".go")) return "go";
  if (n.endsWith(".rs")) return "rust";
  return "python";
}

async function judge0Execute(payload) {
  const files = Array.isArray(payload.files) ? payload.files : [];
  const first = files[0] || {};
  const filename = first.name || "main.py";
  const content = first.content || "";
  const lang = payload.language || guessLanguageFromFilename(filename);
  const language_id = LANGUAGE_ID[lang];
  if (!language_id) {
    const supported = Object.keys(LANGUAGE_ID).sort();
    throw new Error(`Unsupported language '${lang}'. Supported: ${supported.join(", ")}`);
  }

  const stdin = payload.stdin || "";

  // Note: Judge0 supports `cpu_time_limit` (seconds) and `wall_time_limit` (seconds).
  // We'll map run_timeout(ms) to wall_time_limit, with reasonable bounds.
  const runTimeoutMs = Number(payload.run_timeout ?? 5000);
  const wall_time_limit = Math.max(1, Math.min(30, Math.ceil(runTimeoutMs / 1000)));

  const submission = {
    source_code: content,
    language_id,
    stdin,
    wall_time_limit,
  };

  // Only set expected_output if caller provided it; otherwise Judge0 may mark
  // non-empty stdout as "Wrong Answer" against an empty expected output.
  if (typeof payload.expected_output === "string") {
    submission.expected_output = payload.expected_output;
  }

  const url = new URL(`${BASE_URL}/submissions`);
  url.searchParams.set("base64_encoded", "false");
  url.searchParams.set("wait", "true");

  const resp = await fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(submission),
  });

  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`Judge0 HTTP ${resp.status}: ${text.substring(0, 400)}`);
  }

  let result;
  try {
    result = JSON.parse(text);
  } catch {
    throw new Error("Judge0 returned non-JSON response");
  }

  const stdout = decodeB64Maybe(result.stdout);
  const stderr = decodeB64Maybe(result.stderr) || decodeB64Maybe(result.compile_output);

  // Judge0 result.status.id:
  // 1 in queue, 2 processing, 3 accepted, 4 wrong answer, >4 various errors (CE/RE/TLE/etc).
  const statusId = result?.status?.id;

  // For our UI: code=0 means "verification passed".
  // Treat "Wrong Answer" as failure; that is exactly how we detect mismatches
  // between bounty requirements (expected_output / tests) and the submission.
  const code = statusId === 3 ? 0 : 1;

  return {
    run: {
      stdout: stdout || "",
      stderr: stderr || "",
      code,
    },
    judge0: {
      status: result.status || null,
      status_id: statusId ?? null,
      time: result.time ?? null,
      memory: result.memory ?? null,
      language_id,
    },
  };
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS_HEADERS);
    res.end();
    return;
  }

  if (req.method === "GET" && req.url === "/health") {
    json(res, 200, { status: "ok", proxy: "judge0_proxy", port: PORT, baseUrl: BASE_URL });
    return;
  }

  if (req.method !== "POST" || req.url !== "/execute") {
    json(res, 404, { error: "Use POST /execute or GET /health" });
    return;
  }

  collectJson(req, res, async (payload) => {
    try {
      const out = await judge0Execute(payload);
      json(res, 200, out);
      const lang = payload.language || guessLanguageFromFilename(payload?.files?.[0]?.name);
      console.log(`[Judge0Proxy] ${new Date().toISOString()} | ${lang} | ok`);
    } catch (e) {
      json(res, 502, { error: e?.message || String(e) });
      console.error(`[Judge0Proxy] ${new Date().toISOString()} | error: ${e?.message || e}`);
    }
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log("╔══════════════════════════════════════════╗");
  console.log("║   Bounty Escrow — Judge0 Proxy Server    ║");
  console.log(`║   Listening on http://localhost:${PORT}   ║`);
  console.log(`║   Forwarding → ${BASE_URL}                ║`);
  console.log("║   Press Ctrl+C to stop                   ║");
  console.log("╚══════════════════════════════════════════╝");
});

