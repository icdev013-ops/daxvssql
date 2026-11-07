/**
 * Sliding-window limiter, per process. executeQueries allows 120 requests per minute per identity, and the
 * service principal is shared with production, so the POC caps itself at 30 a minute (route and reference
 * queries together), leaving production at least 90.
 */
export class SlidingWindowLimiter {
  private stamps: number[] = [];
  private chain: Promise<void> = Promise.resolve();
  public waitedMs = 0;

  constructor(private readonly max: number, private readonly windowMs: number) {}

  /** Resolves when a slot is free; calls are served in arrival order. Returns the ms spent waiting. */
  acquire(): Promise<number> {
    const run = async (): Promise<number> => {
      const started = Date.now();
      for (;;) {
        const now = Date.now();
        this.stamps = this.stamps.filter((t) => now - t < this.windowMs);
        if (this.stamps.length < this.max) {
          this.stamps.push(now);
          const waited = Date.now() - started;
          this.waitedMs += waited;
          return waited;
        }
        const wait = this.windowMs - (now - this.stamps[0]) + 5;
        await new Promise((r) => setTimeout(r, wait));
      }
    };
    const p = this.chain.then(run);
    this.chain = p.then(() => undefined, () => undefined);
    return p;
  }
}

export const DAX_QUERIES_PER_MINUTE = 30;
export const daxLimiter = new SlidingWindowLimiter(DAX_QUERIES_PER_MINUTE, 60_000);
