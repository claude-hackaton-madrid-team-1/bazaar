// The argument reading every `jev-*` command shares, so five commands do not carry five parsers
// that drift apart. A caller mistake is a `JevUsageError`, which every command turns into exit 2.
import { readFileSync } from "node:fs";

import { JevUsageError } from "./judge";

export type JevFlagSpec = {
  /** Flags that consume the next argument. */
  readonly withValue: readonly string[];
  /** Flags that are on or off. */
  readonly switches: readonly string[];
  readonly usage: string;
};

export type JevFlags = {
  readonly value: (flag: string) => string | undefined;
  readonly has: (flag: string) => boolean;
};

/**
 * One pass over argv. An unknown flag, a value flag with nothing after it, and a value that is
 * itself a flag are all refused: a swallowed value is how a command silently reads the wrong file.
 */
export function parseJevFlags(argv: readonly string[], spec: JevFlagSpec): JevFlags {
  const values = new Map<string, string>();
  const switches = new Set<string>();
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index] ?? "";
    if (spec.switches.includes(flag)) {
      switches.add(flag);
      continue;
    }
    if (!spec.withValue.includes(flag)) {
      throw new JevUsageError(`unknown option ${flag}\n\n${spec.usage}`);
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) {
      throw new JevUsageError(`${flag} needs a value\n\n${spec.usage}`);
    }
    values.set(flag, value);
    index += 1;
  }
  return { value: (flag) => values.get(flag), has: (flag) => switches.has(flag) };
}

export function jevNumberFlag(flags: JevFlags, flag: string): number | undefined {
  const raw = flags.value(flag);
  if (raw === undefined) return undefined;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new JevUsageError(`${flag} needs a number`);
  return value;
}

/** A threshold, a weight or any other 0 to 1 flag. */
export function jevFractionFlag(flags: JevFlags, flag: string): number | undefined {
  const value = jevNumberFlag(flags, flag);
  if (value === undefined) return undefined;
  if (value < 0 || value > 1) throw new JevUsageError(`${flag} must be between 0 and 1`);
  return value;
}

export function jevTextFileAt(path: string): string {
  try {
    return readFileSync(path, "utf8");
  } catch {
    throw new JevUsageError(`cannot read ${path}`);
  }
}

/** The JSON object a command was pointed at. An array or a bare value is a caller mistake. */
export function jevJsonFileAt(path: string): Record<string, unknown> {
  const text = jevTextFileAt(path);
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new JevUsageError(`${path} is not JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new JevUsageError(`${path} must hold a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

/** Exit 0 for any verdict, 2 for a caller mistake. The message never carries the key. */
export function runJevCli(main: (argv: readonly string[]) => Promise<number>): void {
  main(process.argv.slice(2))
    .then((code) => {
      process.exit(code);
    })
    .catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      process.stderr.write(`${message}\n`);
      process.exit(2);
    });
}
