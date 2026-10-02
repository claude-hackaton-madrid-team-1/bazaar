// The one filter for text that leaves the machine in a report or public artifact: it removes Party,
// account, contract and command ids, internal hostnames, database names and credential-shaped values,
// strips markup, and refuses output that still carries a prohibited value.
const PARTY_ID_PATTERN_SOURCE = "[A-Za-z0-9][A-Za-z0-9._-]*::[A-Za-z0-9][A-Za-z0-9._-]*";

// A credential key may carry a prefix (`client_secret`, `refreshToken`) and sit inside JSON quotes,
// escaped or not, before its separator and its value.
const CREDENTIAL_KEY_VALUE = String.raw`\b[a-z0-9_-]*(?:api[_-]?key|secret|token|password|passphrase|private[_-]?key|signing[_-]?key)["'\\]*\s*[:=]\s*["'\\]*[^\s"',;\\]+`;

const PROHIBITED_PUBLIC_PATTERNS: ReadonlyArray<{ label: string; pattern: RegExp }> = [
  // Match the whole block. Without an END marker, consume the rest of the text rather than leak it.
  {
    label: "private key",
    pattern: /-----BEGIN ([A-Z ]*PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|$)/giu,
  },
  { label: "account id", pattern: /\bacct_(?:[a-z0-9_-]+|…+|\.\.\.+)/giu },
  // Mask identifier-shaped Party values in free text.
  { label: "party id", pattern: new RegExp(String.raw`\b${PARTY_ID_PATTERN_SOURCE}`, "gu") },
  {
    label: "contract id",
    pattern:
      /\b(?:00[a-f0-9]{40,}|(?:cid|contract(?:_?id)?)[_:=/-][a-z0-9][a-z0-9._:/+-]{7,})\b/giu,
  },
  {
    label: "internal hostname",
    pattern:
      /\b(?:localhost(?::[0-9]{2,5})?|(?:10|127)\.[0-9.]+(?::[0-9]{2,5})?|192\.168\.[0-9.]+(?::[0-9]{2,5})?|172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9.]+(?::[0-9]{2,5})?|(?:[a-z0-9-]+\.)+(?:internal|local))\b/giu,
  },
  {
    label: "database name",
    pattern:
      /\b(?:(?:database|db)(?:_?name)?\s*[:=]\s*["']?[a-z][a-z0-9_-]{5,})\b/giu,
  },
  {
    label: "command id",
    pattern:
      /\b(?:cmd_[a-z0-9][a-z0-9_-]{5,}|command(?:_?id)?\s*[:=]\s*["']?[a-z0-9][a-z0-9._:-]{7,})\b/giu,
  },
  {
    label: "credential-shaped value",
    pattern: new RegExp(
      String.raw`(?:\bBearer\s+[a-z0-9._~+/-]+=*|\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|${CREDENTIAL_KEY_VALUE}|\b(?:sk|pk)_(?:live|test|prod)_[a-z0-9_-]{8,}|\b[a-z][a-z0-9+.-]*:\/\/[^\s/:]+:[^\s/@]+@[^\s/]+)`,
      "giu",
    ),
  },
];

/** The redaction pass alone, for a caller that keeps its own whitespace and markup rules. */
export function redactProhibitedValues(input: string): string {
  let output = input;
  for (const { pattern } of PROHIBITED_PUBLIC_PATTERNS)
    output = output.replace(pattern, "[redacted]");
  return output;
}

/** The label of the first prohibited value in `value`, or undefined when it carries none. */
export function prohibitedPublicValueLabel(value: string): string | undefined {
  for (const { label, pattern } of PROHIBITED_PUBLIC_PATTERNS) {
    pattern.lastIndex = 0;
    if (pattern.test(value)) return label;
  }
  return undefined;
}

export function sanitizePublicText(input: string): string {
  const stripped = input
    .normalize("NFKC")
    .replace(/!\[([^\]]*)\]\([^)]*\)/gu, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/gu, "$1")
    // ponytail: only strip tag-shaped markup; a broad `<...>` match corrupts measured
    // comparisons such as `age < 5000 ms, ticks >= 2` in the operator evidence.
    .replace(/<\/?[A-Za-z][^>]*>/gu, " ");
  const output = redactProhibitedValues(stripped)
    .replace(/`/gu, "")
    .replace(/\*\*|__|~~/gu, "")
    .split("")
    .map((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127 ? " " : character;
    })
    .join("")
    .replace(/\s+/gu, " ")
    .trim();
  assertNoProhibitedPublicValue(output);
  return output || "[redacted]";
}

export function assertNoProhibitedPublicValue(value: string): void {
  const label = prohibitedPublicValueLabel(value);
  if (label !== undefined) throw new Error(`public artifact contains prohibited ${label}`);
}
