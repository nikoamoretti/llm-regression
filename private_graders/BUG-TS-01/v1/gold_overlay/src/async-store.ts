export type Fetcher = () => Promise<string>;

export class AsyncStore {
  private value: string | null = null;
  private inflight = 0;
  private generation = 0;

  current(): string | null {
    return this.value;
  }

  async load(fetcher: Fetcher): Promise<string> {
    const requestId = ++this.generation;
    this.inflight += 1;
    try {
      const result = await fetcher();
      if (requestId === this.generation) {
        this.value = result;
      }
      return result;
    } finally {
      this.inflight -= 1;
    }
  }

  pending(): number {
    return this.inflight;
  }
}
