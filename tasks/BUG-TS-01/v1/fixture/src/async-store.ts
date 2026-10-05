export type Fetcher = () => Promise<string>;

export class AsyncStore {
  private value: string | null = null;
  private inflight = 0;

  current(): string | null {
    return this.value;
  }

  async load(fetcher: Fetcher): Promise<string> {
    this.inflight += 1;
    const result = await fetcher();
    // BUG: last writer wins even when the writer started first.
    this.value = result;
    this.inflight -= 1;
    return result;
  }

  pending(): number {
    return this.inflight;
  }
}
