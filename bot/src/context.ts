/**
 * The per-request [CONTEXT: ...] prefix on the user message. The date always comes from the caller's anchor
 * date (YYYY-MM-DD), never the system clock.
 *
 *  - productionContextLine: route.ts at d1b6a70, byte for byte apart from the date source (A0).
 *  - fairContextLine: one line shared by A and B: the same fields, the 2026-11-07 date-basis decision, and no
 *    DAX column or tool names ("the query tool"), so the two arms read an identical line.
 */
export const SALES_BASIS: Record<string, "sold" | "created"> = {
  // Decision 2026-11-07: ICLA on created date; every other franchise on sold date.
  ICLA: "created", ICAU: "sold", ICCH: "sold", ICDF: "sold", ICSE: "sold", ICTA: "sold",
};

export interface Anchor { iso: string; year: number; month: number; day: number }

export function parseAnchor(iso: string): Anchor {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!m) throw new Error(`anchor date must be YYYY-MM-DD (got ${JSON.stringify(iso)})`);
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const d = new Date(Date.UTC(year, month - 1, day));
  if (d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) throw new Error(`invalid anchor date ${iso}`);
  return { iso, year, month, day };
}

export function productionContextLine(
  anchor: Anchor, branch: string, meta: { displayName: string; branchLabel: string }, liveOk: boolean,
  soldDateBranches: string[],
): string {
  const liveLine = liveOk
    ? "live_data=AVAILABLE (use query_dax for any numeric/ranking/comparison question; the screenshot may be stale)"
    : "live_data=NOT_CONFIGURED (answer from screenshot + semantic model only; do not pretend to query)";
  return (
    `[CONTEXT: today=${anchor.iso} year=${anchor.year} prev_year=${anchor.year - 1} month=${anchor.month} ` +
    `active_branch=${branch} (${meta.displayName}, ${meta.branchLabel}) ${liveLine}. ` +
    (soldDateBranches.includes(branch)
      ? "sales_basis=SOLD_DATE (this franchise reports sold revenue, sold counts and designer sold rankings by Salesforce_Data[Original Project Sold Date]: use that anchor for ANY 'sold' amount/count/ranking unless the user explicitly says 'created'; this OVERRIDES the created-date default below; it does NOT change the [Closing Rate] DateTable rule). "
      : "sales_basis=CREATED_DATE (this franchise reports sold figures by Salesforce_Data[Created Date]). ") +
    "screenshot=NONE (no screenshot is attached and the user CANNOT attach one: never ask for a screenshot or a recapture; answer with query_dax, or ask a clarifying question about the metric or time period). " +
    `Substitute {YEAR}=${anchor.year} and {PREV_YEAR}=${anchor.year - 1} in cookbook examples below.]`
  );
}

export function fairContextLine(
  anchor: Anchor, branch: string, meta: { displayName: string; branchLabel: string }, liveOk: boolean,
): string {
  const basis = SALES_BASIS[branch] ?? "created";
  const liveLine = liveOk
    ? "live_data=AVAILABLE (use the query tool for any numeric/ranking/comparison question; the screenshot may be stale)"
    : "live_data=NOT_CONFIGURED (answer from the schema only; do not pretend to query)";
  const basisLine = basis === "sold"
    ? "sales_basis=SOLD_DATE (sales in a period count on the project's original sold date; name the basis and end with \"Want this by created date too?\"; Lost / Dead Lead / Proposed always use the created date; closing-rate rules unchanged). "
    : "sales_basis=CREATED_DATE (sales in a period count on the project's created date; name the basis and end with \"Want this by sold date too?\"; Lost / Dead Lead / Proposed always use the created date; closing-rate rules unchanged). ";
  return (
    `[CONTEXT: today=${anchor.iso} year=${anchor.year} prev_year=${anchor.year - 1} month=${anchor.month} ` +
    `active_branch=${branch} (${meta.displayName}, ${meta.branchLabel}) ${liveLine}. ` +
    basisLine +
    "screenshot=NONE (no screenshot is attached and the user CANNOT attach one: never ask for a screenshot or a recapture; answer with the query tool, or ask a clarifying question about the metric or time period). " +
    `Substitute {YEAR}=${anchor.year} and {PREV_YEAR}=${anchor.year - 1} in cookbook examples below.]`
  );
}

/** {YEAR}/{PREV_YEAR}/{MONTH} leak repair from production tools.ts, on the anchor date instead of the UTC clock. */
export function substitutePlaceholders(text: string, anchor: Anchor): { text: string; substituted: boolean } {
  if (!text.includes("{")) return { text, substituted: false };
  let did = false;
  const out = text.replace(/\{(YEAR|PREV_YEAR|MONTH)\}/g, (_m, key: string) => {
    did = true;
    if (key === "YEAR") return String(anchor.year);
    if (key === "PREV_YEAR") return String(anchor.year - 1);
    return String(anchor.month);
  });
  return { text: out, substituted: did };
}
