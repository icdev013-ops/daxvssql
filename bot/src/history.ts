/**
 * History handling, ported from route.ts: sanitizeHistory, visibleProseEmpty, the 3-exchange window with the
 * "[N earlier turns omitted]" stub, and the query-log marker the stream carries into later turns.
 *
 * Marker: A0 keeps production's `[DAX_LOG_R<n>: ...]`. The fair arms (A, B) use `[QUERY_LOG_R<n>: ...]` so
 * history no longer says DAX; A's vendored prompt text is rewritten to match (see arms.ts).
 */
export type ChatTurn = { role: "user" | "assistant"; content: string };

export type MarkerStyle = "DAX_LOG" | "QUERY_LOG";

export function queryLogMarker(style: MarkerStyle, round: number, query: string): string {
  const oneLine = query.replace(/\s+/g, " ").trim();
  return `\n\n[${style}_R${round}: ${oneLine}]\n\n`;
}

export const TOOL_MARKER = "\n\n[Looking up live data…]\n\n";
export const CONTINUE_MARKER = "\n\n[CONTINUE_AVAILABLE]\n\n";

export function visibleProseEmpty(text: string): boolean {
  if (!text) return true;
  const stripped = text
    .replace(/\[(?:DAX|QUERY)_LOG_R\d+:[^\n]*\]/g, "")
    .replace(/```viz[\s\S]*?```/g, "")
    .replace(/\[Looking up live data…\]/g, "")
    .replace(/\[CONTINUE_AVAILABLE\]/g, "")
    .trim();
  return stripped.length === 0;
}

/** The answer as the owner would see it: markers, viz JSON and status sentinels removed. */
export function visibleAnswer(text: string): string {
  return text
    .replace(/\[(?:DAX|QUERY)_LOG_R\d+:[^\n]*\]/g, "")
    .replace(/```viz[\s\S]*?```/g, "")
    .replace(/\[Looking up live data…\]/g, "")
    .replace(/\[CONTINUE_AVAILABLE\]/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function sanitizeHistory(turns: ChatTurn[], maxVizChars: number): ChatTurn[] {
  const out: ChatTurn[] = [];
  for (const t of turns) {
    if (t.role !== "user" && t.role !== "assistant") continue;
    const raw = typeof t.content === "string" ? t.content : "";
    if (t.role === "assistant") {
      const content = raw
        .replace(/```viz[\s\S]*?```/g, (m) => (m.length > maxVizChars ? "" : m))
        .trim();
      const dead = visibleProseEmpty(content) || /^_\((error:|stopped)/.test(content);
      if (dead) {
        if (out.length && out[out.length - 1].role === "user") out.pop();
        continue;
      }
      if (!out.length || out[out.length - 1].role !== "user") continue;
      out.push({ role: "assistant", content });
    } else {
      const content = raw.trim();
      if (!content) continue;
      if (out.length && out[out.length - 1].role === "user") out.pop();
      out.push({ role: "user", content });
    }
  }
  if (out.length && out[out.length - 1].role === "user") out.pop();
  return out;
}

/** Sanitise, keep the last `maxTurns` exchanges, and say how many turns were dropped. */
export function windowHistory(turns: ChatTurn[], maxTurns: number, maxVizChars: number):
  { messages: ChatTurn[]; kept: ChatTurn[]; omitted: number } {
  const all = sanitizeHistory(turns, maxVizChars);
  const cap = maxTurns * 2;
  const kept = all.slice(-cap);
  const omitted = Math.max(0, all.length - cap);
  const messages: ChatTurn[] = [];
  if (omitted > 0) {
    messages.push({
      role: "user",
      content: `[${omitted} earlier conversation turn${omitted === 1 ? "" : "s"} omitted for brevity. Ask the user to re-state any earlier context if you need it.]`,
    });
  }
  messages.push(...kept);
  return { messages, kept, omitted };
}
