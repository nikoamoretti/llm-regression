type Bucket = { tokens: number; updatedAt: number };

export class RateLimiter {
  private readonly burst: number;
  private readonly refillPerSecond: number;
  private readonly clock: () => number;
  private readonly buckets = new Map<string, Bucket>();

  constructor(
    burst: number,
    refillPerSecond: number,
    clock: () => number = () => Date.now() / 1000,
  ) {
    this.burst = burst;
    this.refillPerSecond = refillPerSecond;
    this.clock = clock;
  }

  allow(key: string, cost = 1): boolean {
    const now = this.clock();
    const current = this.buckets.get(key) ?? { tokens: this.burst, updatedAt: now };
    const elapsed = Math.max(0, now - current.updatedAt);
    const refilled = Math.min(this.burst, current.tokens + elapsed * this.refillPerSecond);
    if (refilled < cost) {
      this.buckets.set(key, { tokens: refilled, updatedAt: now });
      return false;
    }
    this.buckets.set(key, { tokens: refilled - cost, updatedAt: now });
    return true;
  }
}
