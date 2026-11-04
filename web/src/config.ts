// Default settings (exported from config.example.yaml defaults by scripts/export_web_content.py).

import DEFAULTS from './content/config.json';
import { Config } from './engine/execution';

type Plain = Record<string, unknown>;

function merge(base: Plain, over: Plain): Plain {
  const out: Plain = structuredClone(base);
  for (const [k, v] of Object.entries(over ?? {})) {
    out[k] = v && typeof v === 'object' && !Array.isArray(v) && typeof out[k] === 'object'
      ? merge(out[k] as Plain, v as Plain)
      : v;
  }
  return out;
}

/** Default config with nested overrides applied (in order). */
export function configWith(...overrides: Plain[]): Config {
  let cfg = structuredClone(DEFAULTS) as unknown as Plain;
  for (const o of overrides) cfg = merge(cfg, o);
  return cfg as unknown as Config;
}
