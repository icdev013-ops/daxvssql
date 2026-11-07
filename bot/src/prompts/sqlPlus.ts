/**
 * Arm B+ prompt section: the governed metric views of sandbox/views_plus (schemas pocplus_<ent>).
 *
 * B+ = arm B's round-0 and round-1+ prompts and shared rules, byte for byte, plus this one section appended after
 * them in both rounds. The section is sandbox/views_plus/METRIC_VIEWS.md (the views' own documentation: columns,
 * formulas, when to prefer them) with {A} / {Y} / {M} filled from the anchor date in the same SQL syntax the
 * shared rules use (rules.ts). It names no person and holds no data; ICLA's excluded staff list stays inside the
 * view definition, which the B+ account cannot read.
 */
import fs from "node:fs";
import type { Anchor } from "../context";
import { METRIC_VIEWS_DOC } from "../paths";
import { sha256 } from "../util";

export interface MetricViewsDoc { path: string; text: string; sha256: string }
export interface RenderedMetricViews { text: string; sha256: string; unfilled: string[] }

export function loadMetricViewsDoc(file: string = METRIC_VIEWS_DOC): MetricViewsDoc {
  const text = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n").trim();
  if (!text) throw new Error(`${file} is empty`);
  return { path: file, text, sha256: sha256(text) };
}

/** Fill {A} (DATE 'yyyy-mm-dd'), {Y} and {M} from the anchor; any other {UPPER} token is reported as unfilled. */
export function renderMetricViews(doc: MetricViewsDoc, anchor: Anchor | undefined): RenderedMetricViews {
  const values: Record<string, string> = anchor
    ? { A: `DATE '${anchor.iso}'`, Y: String(anchor.year), M: String(anchor.month) }
    : {};
  const text = doc.text.replace(/\{([A-Z][A-Z_]*)\}/g, (m, key: string) => values[key] ?? m);
  const unfilled = [...new Set([...text.matchAll(/\{([A-Z][A-Z_]*)\}/g)].map((m) => m[1]))];
  return { text, sha256: sha256(text), unfilled };
}

/** The block appended to arm B's prompts (after the shared rules) to make arm B+. */
export function metricViewsBlock(r: RenderedMetricViews): string {
  return "\n\n=== GOVERNED METRIC VIEWS (this franchise's schema also holds these views: for the questions listed here, " +
    "query them instead of rebuilding the rules from the bindings above) ===\n" + r.text;
}
