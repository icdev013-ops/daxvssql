/**
 * query_dax: executeQueries against the franchise's published Power BI dataset with the portal's service
 * principal. A port of production lib/copilot/pbi-dax.ts executeDax + pbi-token.ts, with three POC changes:
 *   - throttled to 30 queries a minute per process (shared identity with production);
 *   - an `error` object inside an HTTP 200 body is treated as an error (production would read it as 0 rows);
 *   - the raw response is kept (capped) for the run log.
 * closeOpenParens is applied only when the arm's vendored pbi-dax.ts has it (working tree yes, d1b6a70 no).
 *
 * Read-only: executeQueries cannot write, and the only other call is one GET of the refresh history per
 * franchise per run (for the data version).
 */
import { hasPbiDataset, pbiCredentials, pbiDatasetRef } from "../env";
import { MAX_ROWS_RETURNED, type QueryResult } from "../format";
import { daxLimiter } from "./throttle";

const EXECUTE_QUERIES_TIMEOUT_MS = 30_000;
const SCOPE = "https://analysis.windows.net/powerbi/api/.default";
const RAW_CAP_CHARS = 20_000;

let cachedToken: string | null = null;
let cachedExpiry = 0;

async function getPbiAccessToken(): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  if (cachedToken && now < cachedExpiry) return cachedToken;
  const { tenant, clientId, clientSecret } = pbiCredentials();
  const res = await fetch(`https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "client_credentials", client_id: clientId, client_secret: clientSecret, scope: SCOPE }),
  });
  const json = (await res.json().catch(() => ({}))) as { access_token?: string; expires_in?: number; error?: string; error_description?: string };
  if (!res.ok || !json.access_token) {
    throw new Error(`Power BI auth failed: ${json.error ?? `HTTP ${res.status}`}`);
  }
  cachedToken = json.access_token;
  cachedExpiry = now + (json.expires_in ?? 3600) - 60;
  return cachedToken;
}

export interface DaxExecution extends QueryResult {
  /** The query text actually sent (after closeOpenParens). */
  executed: string;
  /** Raw executeQueries body (or error body), capped. */
  raw?: string;
  throttleWaitMs: number;
  httpStatus?: number;
}

export function daxLiveAvailable(branch: string): boolean {
  return hasPbiDataset(branch);
}

export async function executeDax(
  branch: string, dax: string, opts: { closeOpenParens?: (q: string) => string } = {},
): Promise<DaxExecution> {
  const trimmedIn = dax.trim();
  const trimmed = opts.closeOpenParens ? opts.closeOpenParens(trimmedIn) : trimmedIn;
  if (!trimmed) return { error: "Empty DAX query.", latencyMs: 0, executed: "", throttleWaitMs: 0 };

  let workspaceId: string, datasetId: string;
  try {
    ({ workspaceId, datasetId } = pbiDatasetRef(branch));
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err), latencyMs: 0, executed: trimmed, throttleWaitMs: 0 };
  }

  const throttleWaitMs = await daxLimiter.acquire();
  const startedAt = Date.now();
  let token: string;
  try {
    token = await getPbiAccessToken();
  } catch (err) {
    return { error: `Live data unavailable: ${err instanceof Error ? err.message : String(err)}`, latencyMs: Date.now() - startedAt, executed: trimmed, throttleWaitMs };
  }

  let res: Response;
  try {
    res = await fetch(`https://api.powerbi.com/v1.0/myorg/groups/${workspaceId}/datasets/${datasetId}/executeQueries`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ queries: [{ query: trimmed }], serializerSettings: { includeNulls: true } }),
      signal: AbortSignal.timeout(EXECUTE_QUERIES_TIMEOUT_MS),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const error = /timeout|abort/i.test(msg)
      ? "Query timed out (30s) — simplify the DAX, narrow the date range, or filter to fewer rows."
      : `Network error reaching Power BI: ${msg}`;
    return { error, latencyMs: Date.now() - startedAt, executed: trimmed, throttleWaitMs };
  }
  const latencyMs = Date.now() - startedAt;
  const base = { executed: trimmed, throttleWaitMs, httpStatus: res.status };

  if (res.status === 401) {
    return { ...base, latencyMs, error: "Power BI rejected the service principal (401). Verify SalesforceBI-MCP is a Member of the workspace." };
  }
  if (res.status === 404) {
    return { ...base, latencyMs, error: `Power BI dataset not found (404) for ${branch}. Verify PBI_DS_${branch} env var.` };
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    const snippet = detail.slice(0, 600);
    return { ...base, latencyMs, raw: detail.slice(0, RAW_CAP_CHARS), error: `Power BI returned ${res.status}: ${snippet || "(no body)"}` };
  }

  const text = await res.text().catch(() => "");
  type PbiResponse = {
    error?: unknown;
    results?: Array<{ error?: unknown; tables?: Array<{ rows?: Record<string, unknown>[]; error?: unknown }> }>;
  };
  let json: PbiResponse = {};
  try { json = JSON.parse(text) as PbiResponse; } catch { /* treated as empty, as production does */ }
  const embedded = json.error ?? json.results?.[0]?.error ?? json.results?.[0]?.tables?.[0]?.error;
  if (embedded) {
    // POC change: production ignores this and reports "No rows returned.".
    const detail = typeof embedded === "string" ? embedded : JSON.stringify(embedded);
    return { ...base, latencyMs, raw: text.slice(0, RAW_CAP_CHARS), error: `Power BI returned an error: ${detail.slice(0, 600)}` };
  }
  const rawRows = json.results?.[0]?.tables?.[0]?.rows ?? [];
  const rowCount = rawRows.length;
  const truncated = rowCount > MAX_ROWS_RETURNED;
  const rows = truncated ? rawRows.slice(0, MAX_ROWS_RETURNED) : rawRows;
  const columns = rows[0] ? Object.keys(rows[0]) : [];
  return { ...base, columns, rows, rowCount, truncated, latencyMs, raw: text.slice(0, RAW_CAP_CHARS) };
}

/** Last refresh of the franchise's dataset, for the data version of Route A turns. Cached per franchise per process. */
const refreshCache = new Map<string, Promise<Record<string, unknown>>>();
export function datasetRefreshInfo(branch: string): Promise<Record<string, unknown>> {
  let p = refreshCache.get(branch);
  if (!p) {
    p = (async () => {
      try {
        const { workspaceId, datasetId } = pbiDatasetRef(branch);
        const token = await getPbiAccessToken();
        const res = await fetch(
          `https://api.powerbi.com/v1.0/myorg/groups/${workspaceId}/datasets/${datasetId}/refreshes?$top=5`,
          { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(20_000) },
        );
        if (!res.ok) return { source: "pbi_refresh_history", error: `HTTP ${res.status}`, fetched_at: new Date().toISOString() };
        const json = (await res.json()) as { value?: Array<{ status?: string; startTime?: string; endTime?: string; refreshType?: string }> };
        const list = json.value ?? [];
        const lastCompleted = list.find((r) => r.status === "Completed");
        return {
          source: "pbi_refresh_history",
          dataset_id: datasetId,
          last_completed_end: lastCompleted?.endTime ?? null,
          latest: list[0] ? { status: list[0].status, start: list[0].startTime, end: list[0].endTime ?? null, type: list[0].refreshType } : null,
          fetched_at: new Date().toISOString(),
        };
      } catch (err) {
        return { source: "pbi_refresh_history", error: err instanceof Error ? err.message : String(err), fetched_at: new Date().toISOString() };
      }
    })();
    refreshCache.set(branch, p);
  }
  return p;
}
