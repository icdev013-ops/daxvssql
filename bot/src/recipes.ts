/**
 * A0 recipe fast path: a port of production lib/copilot/recipe-matcher.ts (tryMatchRecipe) plus the route's
 * gates, over the vendored d1b6a70 RECIPES.
 *
 * Pass 1, regex: runs locally, unchanged (intent gate, entity gate, named-group placeholders).
 * Pass 2, embedding: production queries an Upstash Vector index (index `copilot-recipes-v2`, hosted embedding
 *   model BAAI/bge-large-en-v1.5, top-1, similarity >= 0.90, then skipEmbedding and the "every content word is
 *   covered by the recipe's phrases" check). No Supabase is involved. The POC can call the same index read-only
 *   (credentials from the SAAS .env.local) when embeddings are enabled; it is OFF unless the caller enables it,
 *   because that index belongs to the production chatbot.
 * Change from production: the context placeholders (YEAR, MONTH, PREV_MONTH, MONTH_PARENT_YEAR, PREV_YEAR) come
 *   from the anchor date instead of the UTC clock.
 */
import type { Anchor } from "./context";
import { upstashCredentials } from "./env";
import type { DaxRecipe, Intent, VendorBundle } from "./vendor";

export interface MatcherResult {
  recipe: DaxRecipe;
  version: string;
  method: "regex" | "embedding";
  placeholders: Record<string, string>;
  matchedPhrase: string;
  similarity?: number;
}

const EMBEDDING_SIMILARITY_THRESHOLD = 0.9;

const QUESTION_FILLER = new Set(("a an the of for in on at to by with and or is are was were be been do does did " +
  "we our us i me my you your it its this that these those there here what whats which who how many much " +
  "show tell give get list please can could would will should want need see find current currently today " +
  "so far right now total number count all any across between from about per each every over under since than " +
  "into out up as if when where while").split(" "));
function contentWords(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9%]+/g) ?? [])
    .filter((w) => w.length > 1 && !QUESTION_FILLER.has(w))
    .map((w) => (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1) : w));
}
function recipeCoversQuestion(recipe: { title: string; matches: readonly string[] }, question: string): boolean {
  const vocab = new Set(contentWords([recipe.title, ...recipe.matches].join(" ")));
  return contentWords(question).every((w) => vocab.has(w));
}

function intentGateOk(recipe: DaxRecipe, classes: string[]): boolean {
  if (!recipe.intents || recipe.intents.length === 0) return true;
  if (classes.length === 0) return true;
  return recipe.intents.some((rc) => classes.includes(rc));
}
function entityGateOk(recipe: DaxRecipe, branch: string): boolean {
  if (!recipe.entities || recipe.entities.length === 0) return true;
  return recipe.entities.includes(branch);
}

export function contextPlaceholders(branch: string, anchor: Anchor): Record<string, string> {
  const prevMonth = anchor.month === 1 ? 12 : anchor.month - 1;
  const monthParentYear = anchor.month === 1 ? anchor.year - 1 : anchor.year;
  return {
    YEAR: String(anchor.year), MONTH: String(anchor.month), PREV_YEAR: String(anchor.year - 1),
    PREV_MONTH: String(prevMonth), MONTH_PARENT_YEAR: String(monthParentYear), BRANCH: branch,
  };
}

export async function tryMatchRecipe(
  vendor: VendorBundle, question: string, branch: string, classes: string[], anchor: Anchor,
  opts: { embeddings: boolean },
): Promise<{ match: MatcherResult | null; embeddingQueried: boolean; embeddingError?: string }> {
  const q = question.trim();
  if (q.length < 4) return { match: null, embeddingQueried: false };
  const ctx = contextPlaceholders(branch, anchor);

  for (const recipe of vendor.RECIPES) {
    const m = q.match(recipe.matchPattern);
    if (!m) continue;
    if (!intentGateOk(recipe, classes)) continue;
    if (!entityGateOk(recipe, branch)) continue;
    const groups: Record<string, string> = {};
    if (m.groups) for (const [k, v] of Object.entries(m.groups)) if (v !== undefined) groups[k] = v;
    return {
      match: {
        recipe, version: await vendor.recipeVersion(recipe), method: "regex",
        placeholders: { ...recipe.defaults, ...groups, ...ctx }, matchedPhrase: q,
      },
      embeddingQueried: false,
    };
  }

  if (!opts.embeddings) return { match: null, embeddingQueried: false };
  const creds = upstashCredentials();
  if (!creds) return { match: null, embeddingQueried: false, embeddingError: "UPSTASH_VECTOR_* not configured" };
  try {
    const { Index } = await import("@upstash/vector");
    const index = new Index({ url: creds.url, token: creds.token });
    const hits = await index.query({ data: q, topK: 1, includeMetadata: true, includeData: true });
    const top = hits?.[0];
    if (!top || typeof top.score !== "number" || top.score < EMBEDDING_SIMILARITY_THRESHOLD) return { match: null, embeddingQueried: true };
    const recipeId = (top.metadata as { recipeId?: string } | undefined)?.recipeId;
    const recipe = recipeId ? vendor.RECIPES_BY_ID[recipeId] : undefined;
    if (!recipe) return { match: null, embeddingQueried: true };
    if (!intentGateOk(recipe, classes) || !entityGateOk(recipe, branch)) return { match: null, embeddingQueried: true };
    if (recipe.skipEmbedding) return { match: null, embeddingQueried: true };
    if (!recipeCoversQuestion(recipe, q)) return { match: null, embeddingQueried: true };
    return {
      match: {
        recipe, version: await vendor.recipeVersion(recipe), method: "embedding",
        placeholders: { ...recipe.defaults, ...ctx },
        matchedPhrase: typeof top.data === "string" ? top.data : q, similarity: top.score,
      },
      embeddingQueried: true,
    };
  } catch (err) {
    return { match: null, embeddingQueried: true, embeddingError: err instanceof Error ? err.message : String(err) };
  }
}

/** The route's gates on a candidate (d1b6a70: variance, briefing, breakdown). */
export function gateCandidate(
  candidate: MatcherResult | null, intent: Intent, question: string, branchScalarRecipes: string[],
): { matched: MatcherResult | null; skipReason?: string } {
  if (!candidate) return { matched: null };
  const varianceGated = intent.needsVariance && !candidate.recipe.handlesVariance;
  const briefingGated = intent.needsBriefing && !candidate.recipe.handlesBriefing;
  const asksBreakdown =
    /\b(?:by|per|for\s+each|each|every|across|broken?\s+down|breakdown|split)\b[^?]*\b(?:designer|rep|salesperson|consultant|lead\s*source|source|channel|zip|city|month|week|quarter|stage)s?\b/i.test(question)
    || /\b(?:designer|rep|salesperson|consultant)s?['’]?s?\s+(?:closing|close|win)\b/i.test(question);
  const breakdownGated = candidate.method === "embedding" && branchScalarRecipes.includes(candidate.recipe.id) && asksBreakdown;
  if (varianceGated) return { matched: null, skipReason: `variance_gated: ${candidate.recipe.id}` };
  if (briefingGated) return { matched: null, skipReason: `briefing_gated: ${candidate.recipe.id}` };
  if (breakdownGated) return { matched: null, skipReason: `breakdown_gated: ${candidate.recipe.id}` };
  return { matched: candidate };
}
