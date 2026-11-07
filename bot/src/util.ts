import { createHash, randomBytes } from "node:crypto";

export const sha256 = (s: string): string => createHash("sha256").update(s).digest("hex");
export const shortHash = (s: string): string => sha256(s).slice(0, 16);

export function newRunId(label = "run"): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${label}-${randomBytes(2).toString("hex")}`;
}

/** Local calendar date of this PC (for logging whether the anchor matches the clock the databases use). */
export function localIsoDate(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
