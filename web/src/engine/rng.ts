// Seeded random numbers (xoshiro128**). Used for random start dates and the practice market,
// so a session can be replayed exactly from its seed.

function splitmix32(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x9e3779b9) >>> 0;
    let z = s;
    z = Math.imul(z ^ (z >>> 16), 0x85ebca6b) >>> 0;
    z = Math.imul(z ^ (z >>> 13), 0xc2b2ae35) >>> 0;
    return (z ^ (z >>> 16)) >>> 0;
  };
}

export class Rng {
  private s: Uint32Array;
  private spare: number | null = null;

  constructor(seed: number) {
    const sm = splitmix32(seed);
    this.s = new Uint32Array([sm(), sm(), sm(), sm()]);
  }

  private nextU32(): number {
    const s = this.s;
    const result = Math.imul(((Math.imul(s[1], 5) << 7) | (Math.imul(s[1], 5) >>> 25)), 9) >>> 0;
    const t = s[1] << 9;
    s[2] ^= s[0];
    s[3] ^= s[1];
    s[1] ^= s[2];
    s[0] ^= s[3];
    s[2] ^= t;
    s[3] = (s[3] << 11) | (s[3] >>> 21);
    return result;
  }

  /** Uniform in [0, 1). */
  random(): number {
    return (this.nextU32() * 2 ** 21 + (this.nextU32() >>> 11)) / 2 ** 53;
  }

  uniform(lo: number, hi: number): number {
    return lo + (hi - lo) * this.random();
  }

  /** Integer in [lo, hi). */
  integers(lo: number, hi: number): number {
    return lo + Math.floor(this.random() * (hi - lo));
  }

  normal(mean = 0, sd = 1): number {
    if (this.spare !== null) {
      const v = this.spare;
      this.spare = null;
      return mean + sd * v;
    }
    let u = 0;
    while (u === 0) u = this.random();
    const v = this.random();
    const r = Math.sqrt(-2 * Math.log(u));
    this.spare = r * Math.sin(2 * Math.PI * v);
    return mean + sd * r * Math.cos(2 * Math.PI * v);
  }

  choice<T>(items: readonly T[]): T {
    return items[this.integers(0, items.length)];
  }
}

export function randomSeed(): number {
  return Math.floor(Math.random() * 2 ** 31);
}
