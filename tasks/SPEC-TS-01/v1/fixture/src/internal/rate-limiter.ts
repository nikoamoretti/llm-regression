export class RateLimiter {
  constructor(
    _burst: number,
    _refillPerSecond: number,
    _clock: () => number = () => Date.now() / 1000,
  ) {}

  allow(_key: string, _cost = 1): boolean {
    // BUG / incomplete: always admits.
    return true;
  }
}
