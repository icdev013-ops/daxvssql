/**
 * Runtime secrets. Read from the env files at call time, kept in this module only, never printed,
 * never copied into process.env (so child processes and logs cannot pick them up).
 *
 *   SAAS .env.local  -> OPENROUTER_API_KEY, PBI_* (service principal + dataset ids), UPSTASH_VECTOR_*
 *   daxvssql/.env    -> only SANDBOX_ICLA_PASSWORD, SANDBOX_ICCH_PASSWORD, SANDBOX_ICAU_PASSWORD: the three
 *                       SELECT-only franchise accounts. Every other key in that file (the sandbox
 *                       administrator's) is dropped while parsing and never held in memory.
 */
import fs from "node:fs";
import path from "node:path";
import { assertFranchise } from "./franchise";
import { BOT_DIR, POC_DIR, SAAS_DIR } from "./paths";

const FRANCHISE_PASSWORD_KEY = /^SANDBOX_(ICLA|ICCH|ICAU)_PASSWORD$/;

/** Parse KEY=value lines; `keep` decides which keys are retained (the others are never stored). */
function parseEnvFile(file: string, keep: (key: string) => boolean = () => true): Record<string, string> {
  const out: Record<string, string> = {};
  if (!fs.existsSync(file)) return out;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const i = line.indexOf("=");
    const key = line.slice(0, i).trim();
    if (!keep(key)) continue;
    out[key] = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

let saasEnv: Record<string, string> | null = null;
let pocEnv: Record<string, string> | null = null;
const saas = () => (saasEnv ??= parseEnvFile(path.join(SAAS_DIR, ".env.local")));
const poc = () => (pocEnv ??= parseEnvFile(path.join(POC_DIR, ".env"), (k) => FRANCHISE_PASSWORD_KEY.test(k)));

function need(env: Record<string, string>, key: string, where: string): string {
  const v = env[key];
  if (!v) throw new Error(`${key} is missing in ${where}`);
  return v;
}

export function openrouterKey(): string {
  return need(saas(), "OPENROUTER_API_KEY", "the SAAS .env.local");
}

export function pbiCredentials(): { tenant: string; clientId: string; clientSecret: string } {
  const e = saas();
  return {
    tenant: need(e, "PBI_TENANT_ID", "the SAAS .env.local"),
    clientId: need(e, "PBI_CLIENT_ID", "the SAAS .env.local"),
    clientSecret: need(e, "PBI_CLIENT_SECRET", "the SAAS .env.local"),
  };
}

/** Same lookup as production's getDatasetRef (pbi-datasets.ts): PBI_WORKSPACE_ID + PBI_DS_<BRANCH>. */
export function pbiDatasetRef(branch: string): { workspaceId: string; datasetId: string } {
  const e = saas();
  return {
    workspaceId: need(e, "PBI_WORKSPACE_ID", "the SAAS .env.local"),
    datasetId: need(e, `PBI_DS_${branch}`, "the SAAS .env.local"),
  };
}

export function hasPbiDataset(branch: string): boolean {
  const e = saas();
  return !!(e.PBI_WORKSPACE_ID && e[`PBI_DS_${branch}`]);
}

export function upstashCredentials(): { url: string; token: string } | null {
  const e = saas();
  return e.UPSTASH_VECTOR_REST_URL && e.UPSTASH_VECTOR_REST_TOKEN
    ? { url: e.UPSTASH_VECTOR_REST_URL, token: e.UPSTASH_VECTOR_REST_TOKEN }
    : null;
}

/** Password of the franchise's own SELECT-only account. The franchise is validated against the allowlist. */
export function sandboxPassword(entity: string): string {
  const ent = assertFranchise(entity);
  return need(poc(), `SANDBOX_${ent}_PASSWORD`, "daxvssql/.env (created by `python sandbox/sandbox.py apply-views`)");
}

export function hasSandboxPassword(entity: string): boolean {
  try {
    return !!poc()[`SANDBOX_${assertFranchise(entity)}_PASSWORD`];
  } catch {
    return false;
  }
}

/** Keys retained from daxvssql/.env (names only, for the RLS tests). */
export function retainedSandboxKeys(): string[] {
  return Object.keys(poc()).sort();
}

export const _forTests = { parseEnvFile, BOT_DIR };
