#!/usr/bin/env -S bun --no-env-file
// The optional live smoke. TypeSafe publishes no latency figure, so this measures ours.
//
//   TYPESAFE_API_KEY=<key> jev-smoke [--calls 5]
//
// It sends one small request per call, sequentially, and prints the model version, every verdict and
// the latency summary. Without the key it refuses and exits 2 rather than printing a number it did
// not measure.
import { JEV_API_KEY_VARIABLE, type JevQuestions, jevDependenciesFrom, judge } from "./judge";

export const JEV_SMOKE_DEFAULT_CALLS = 5;

/** p50 is the upper median sample, not an interpolation: with five samples that is unambiguous. */
export function jevLatencySummary(samples: readonly number[]): {
  readonly p50: number;
  readonly max: number;
} {
  if (samples.length === 0) throw new Error("no latency samples to summarise");
  const sorted = [...samples].sort((left, right) => left - right);
  return {
    p50: sorted[Math.floor(sorted.length / 2)] ?? 0,
    max: sorted[sorted.length - 1] ?? 0,
  };
}

const SMOKE_STATE = {
  situation:
    "The worker could not continue because the readiness check failed, and the operator has not been asked anything yet.",
  next_step: "Check the configured gRPC endpoint and the readiness reason.",
};

const SMOKE_QUESTIONS: JevQuestions = {
  needs_owner: {
    type: "noul",
    instructions: {
      question: "Reading `situation` and `next_step`, does the next step need the owner?",
    },
    criteria: {
      true: "The next step cannot start until the owner answers or acts.",
      false: "The agent can take the next step by itself with what it already has.",
    },
  },
};

function callsFrom(argv: readonly string[]): number {
  const index = argv.indexOf("--calls");
  if (index < 0) return JEV_SMOKE_DEFAULT_CALLS;
  const value = Number(argv[index + 1]);
  if (!Number.isInteger(value) || value < 1 || value > 20) {
    throw new Error("--calls must be an integer between 1 and 20");
  }
  return value;
}

async function main(argv: readonly string[]): Promise<number> {
  const dependencies = jevDependenciesFrom(process.env);
  if (dependencies.apiKey.length === 0) {
    process.stderr.write(
      `${JEV_API_KEY_VARIABLE} is not in the environment, so there is no live latency to measure.\n`,
    );
    return 2;
  }

  const calls = callsFrom(argv);
  const latencies: number[] = [];
  const models = new Set<string>();
  for (let call = 0; call < calls; call += 1) {
    const result = await judge(
      { state: SMOKE_STATE, questions: SMOKE_QUESTIONS, timeoutMs: 20_000 },
      dependencies,
    );
    latencies.push(result.latencyMs);
    models.add(result.model);
    process.stdout.write(
      `${JSON.stringify({ call: call + 1, latencyMs: result.latencyMs, verdicts: result.verdicts })}\n`,
    );
  }

  process.stdout.write(
    `${JSON.stringify({ calls, models: [...models], latency: jevLatencySummary(latencies) })}\n`,
  );
  return 0;
}

if (import.meta.main) {
  main(process.argv.slice(2))
    .then((code) => {
      process.exit(code);
    })
    .catch((error: unknown) => {
      process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
      process.exit(2);
    });
}
