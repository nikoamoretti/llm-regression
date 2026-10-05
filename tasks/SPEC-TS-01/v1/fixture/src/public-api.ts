export { RateLimiter } from "./internal/rate-limiter.ts";

export function checkLimit(limiter: { allow: (key: string, cost?: number) => boolean }, key: string): boolean {
  return limiter.allow(key, 1);
}
