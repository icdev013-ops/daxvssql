import path from "node:path";
import { fileURLToPath } from "node:url";

/** daxvssql/bot */
export const BOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
/** daxvssql (override with POC_DIR, e.g. when the bot runs from a git worktree of this repo) */
export const POC_DIR = path.resolve(process.env.POC_DIR ?? path.join(BOT_DIR, ".."));
/** The production chatbot repo (read-only). Override with SAAS_DIR. */
export const SAAS_DIR = path.resolve(process.env.SAAS_DIR ?? path.join(POC_DIR, "..", "Inspired Closets SAAS Reporting"));
export const VENDOR_DIR = path.join(BOT_DIR, ".vendor");
export const RUNS_DIR = path.resolve(process.env.POC_RUNS_DIR ?? path.join(POC_DIR, "runs"));
export const SANDBOX_DATA_VERSION = path.join(POC_DIR, ".sandbox", "data_version.json");
export const SCHEMA_DOC = path.join(POC_DIR, "sandbox", "views", "SCHEMA.md");
/** Arm B+: documentation of the governed metric views (sandbox/views_plus), injected into the B+ prompts. */
export const METRIC_VIEWS_DOC = path.join(POC_DIR, "sandbox", "views_plus", "METRIC_VIEWS.md");
