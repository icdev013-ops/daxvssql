/**
 * The franchises in scope. Every entry point (CLI, case files, engine, SQL executor, connection pools) validates
 * the caller-supplied franchise against this list. Model output and tool arguments never choose a franchise.
 */
export const FRANCHISES = ["ICLA", "ICCH", "ICAU"] as const;
export type Franchise = (typeof FRANCHISES)[number];

export function isFranchise(x: unknown): x is Franchise {
  return typeof x === "string" && (FRANCHISES as readonly string[]).includes(x);
}

export function assertFranchise(x: unknown): Franchise {
  if (!isFranchise(x)) {
    throw new Error(`franchise must be one of ${FRANCHISES.join(", ")} (got ${JSON.stringify(x)})`);
  }
  return x;
}
