# Vendored copy of jev-sdk

- Upstream: https://github.com/ogarciarevett/jev-sdk (private)
- Commit: `e155652fabf81d87ed9db8e3f4ef1e9b0a314c09` (`fix(jev): redact complete keys and structured credentials`)
- Copied: `src/`, `test/`, `questions/`, `skills/`, `package.json`, `bun.lock`, `tsconfig.json`, `README.md`, `.gitignore`.
  Not copied: `odd/` (the upstream extraction task note).

The source is vendored instead of installed with `bun add github:ogarciarevett/jev-sdk` because the
upstream repository is private and not every Bazaar teammate can read it. Do not edit files here;
change them upstream and re-copy, then update the commit above.

Bazaar-owned pieces live outside this directory:

- `questions/negotiation.json`: the negotiation question pack.
- `.ai/skills/jev/`: the agent skill, fanned out to every CLI by `sh scripts/sync-ai-docs.sh`.

Run from the repository root:

```sh
cd vendor/jev-sdk && bun install && bun test && bunx tsc --noEmit && cd ../..
printf '%s\n' '{"offer":{},"album":{},"cash":400,"tick":1}' \
  | bun vendor/jev-sdk/src/jev-judge.ts --state - --questions questions/negotiation.json
```

Without `TYPESAFE_API_KEY` in the process environment every verdict is `undecided`.

## Known test difference

Seven `jev-finding` command-line tests fail here and pass upstream (323 of 330 pass here, 330 of
330 upstream). They cite paths such as `src/judge.ts` and read them with `git show HEAD:<path>`,
which assumes the SDK is its own git root. Inside Bazaar the git root is the repository root, so
`jev-finding` needs root-relative paths, for example `--file vendor/jev-sdk/src/judge.ts`.
