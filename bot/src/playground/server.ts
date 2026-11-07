/**
 * Local test page for the DAX vs SQL POC.
 *
 *   npm run playground          ->  http://127.0.0.1:4317
 *
 * Bound to 127.0.0.1 only and refuses any non-loopback client: the SQL route
 * reads the sandbox MySQL, which must stay on this PC. Every turn goes through
 * the same runTurn() the CLI and the scored runs use, and is logged to
 * runs/playground-<date>.jsonl (gitignored).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadArm, type ArmId, type ArmRuntime } from "../arms";
import { parseAnchor } from "../context";
import { runTurn, type TurnRecord } from "../engine";
import { FRANCHISES, isFranchise } from "../franchise";
import type { ChatTurn } from "../history";
import { RunLog } from "../log";
import { loadRulesTemplates } from "../prompts/rules";
import { closePools } from "../tools/sql";

const HOST = "127.0.0.1";
const PORT = Number(process.env.PLAYGROUND_PORT ?? 4317);
const ARMS: ArmId[] = ["A", "B", "A0"];
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGE = path.join(HERE, "index.html");

const rules = loadRulesTemplates();
const armCache = new Map<ArmId, Promise<ArmRuntime>>();
const getArm = (id: ArmId) => {
  let p = armCache.get(id);
  if (!p) { p = loadArm(id, rules); armCache.set(id, p); }
  return p;
};

function pacificToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles" }).format(new Date());
}
const log = new RunLog(`playground-${pacificToday().replaceAll("-", "")}`);

function summarise(rec: TurnRecord) {
  return {
    arm: rec.arm,
    franchise: rec.franchise,
    anchor: rec.anchor_date,
    answer: rec.answer,
    path: rec.path,
    recipe: rec.recipe ? (rec.recipe as { id?: string }).id ?? "recipe" : null,
    latency_ms: rec.latency_ms,
    cost_usd: rec.cost_usd,
    tokens: rec.tokens,
    rounds: rec.round_count,
    guards: rec.guards_fired,
    errors: rec.errors,
    data_version: rec.data_version,
    queries: rec.queries.map((q) => ({
      round: q.round, dialect: q.dialect, query: q.executed ?? q.query, ok: q.ok, rows: q.row_count,
      truncated: q.truncated, error: q.error, blocked_by: q.blocked_by, ms: q.latency_ms,
    })),
  };
}

async function readBody(req: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const c of req) {
    size += (c as Buffer).length;
    if (size > 256_000) throw new Error("request too large");
    chunks.push(c as Buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

function send(res: http.ServerResponse, code: number, body: unknown, type = "application/json") {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  res.end(type === "application/json" ? JSON.stringify(body) : String(body));
}

/** Optional access key (PLAYGROUND_TOKEN). Required whenever the page is exposed through a tunnel:
 *  open /?key=<token> once; the key is kept in an HttpOnly cookie and stripped from the address bar. */
const TOKEN = process.env.PLAYGROUND_TOKEN ?? "";
function keyOk(candidate: string | undefined): boolean {
  if (!TOKEN) return true;
  if (!candidate) return false;
  const a = Buffer.from(candidate), b = Buffer.from(TOKEN);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
function cookieKey(req: http.IncomingMessage): string | undefined {
  const m = /(?:^|;\s*)pg_key=([^;]+)/.exec(req.headers.cookie ?? "");
  return m ? decodeURIComponent(m[1]) : undefined;
}

const server = http.createServer(async (req, res) => {
  const addr = req.socket.remoteAddress ?? "";
  if (!["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(addr)) return send(res, 403, { error: "local only" });
  const url = new URL(req.url ?? "/", "http://local");
  if (TOKEN) {
    const qk = url.searchParams.get("key") ?? undefined;
    if (req.method === "GET" && url.pathname === "/" && qk && keyOk(qk)) {
      const secure = req.headers["x-forwarded-proto"] === "https" || !!req.headers["cf-connecting-ip"];
      res.writeHead(302, {
        Location: "/",
        "Set-Cookie": `pg_key=${encodeURIComponent(qk)}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400${secure ? "; Secure" : ""}`,
        "Cache-Control": "no-store",
      });
      return res.end();
    }
    if (!keyOk(cookieKey(req) ?? (req.headers["x-pg-key"] as string | undefined))) {
      return send(res, 401, "<!doctype html><meta name=viewport content='width=device-width'><title>Locked</title>" +
        "<p style='font:16px system-ui;padding:24px'>This test page needs its access link.</p>", "text/html; charset=utf-8");
    }
  }
  req.url = url.pathname;
  try {
    if (req.method === "GET" && (req.url === "/" || req.url === "/index.html")) {
      return send(res, 200, fs.readFileSync(PAGE, "utf8"), "text/html; charset=utf-8");
    }
    if (req.method === "GET" && req.url === "/api/config") {
      return send(res, 200, { franchises: FRANCHISES, arms: ARMS, today: pacificToday() });
    }
    if (req.method === "POST" && req.url === "/api/ask") {
      const b = (await readBody(req)) as { arm?: string; franchise?: string; anchor?: string; question?: string; history?: ChatTurn[] };
      const arm = String(b.arm ?? "");
      if (!ARMS.includes(arm as ArmId)) return send(res, 400, { error: `arm must be one of ${ARMS.join(", ")}` });
      if (!isFranchise(b.franchise)) return send(res, 400, { error: `franchise must be one of ${FRANCHISES.join(", ")}` });
      const question = String(b.question ?? "").trim();
      if (!question || question.length > 2000) return send(res, 400, { error: "question missing or too long" });
      const anchor = parseAnchor(String(b.anchor || pacificToday())).iso;
      const history = Array.isArray(b.history)
        ? b.history.filter((t) => t && (t.role === "user" || t.role === "assistant") && typeof t.content === "string").slice(-12)
        : [];
      const rec = await runTurn({
        arm: await getArm(arm as ArmId), franchise: b.franchise, anchor, question, history,
        runId: log.runId, caseId: `pg-${Date.now()}`,
      });
      await log.append(rec);
      return send(res, 200, summarise(rec));
    }
    send(res, 404, { error: "not found" });
  } catch (e) {
    send(res, 500, { error: e instanceof Error ? e.message : String(e) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`DAX vs SQL test page: http://${HOST}:${PORT}  (local only; Ctrl+C to stop)`);
  console.log(`turns are logged to ${log.file}`);
});
const shutdown = () => { server.close(); closePools().finally(() => process.exit(0)); };
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
