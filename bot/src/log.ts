/**
 * Run log: one JSONL line per turn in daxvssql/runs/<run-id>.jsonl (gitignored: answers hold real franchise
 * numbers and names). Raw query rows are already capped at 50 per query; long strings are capped here too.
 */
import fs from "node:fs";
import path from "node:path";
import { RUNS_DIR } from "./paths";
import type { TurnRecord } from "./engine";

const MAX_RAW_CHARS = 20_000;

function cap(value: unknown): unknown {
  if (typeof value === "string") return value.length > MAX_RAW_CHARS ? value.slice(0, MAX_RAW_CHARS) + `…[+${value.length - MAX_RAW_CHARS} chars]` : value;
  if (Array.isArray(value)) return value.map(cap);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, cap(v)]));
  return value;
}

export class RunLog {
  readonly file: string;
  private chain: Promise<void> = Promise.resolve();

  constructor(readonly runId: string, dir = RUNS_DIR) {
    fs.mkdirSync(dir, { recursive: true });
    this.file = path.join(dir, `${runId}.jsonl`);
  }

  append(record: TurnRecord): Promise<void> {
    const line = JSON.stringify(cap(record)) + "\n";
    this.chain = this.chain.then(() => fs.promises.appendFile(this.file, line, "utf8"));
    return this.chain;
  }
}
