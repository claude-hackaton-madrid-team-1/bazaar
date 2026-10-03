# Live mode — requires impeccable's optional CLI (not bundled here)

Interactive live variant mode lets you select elements in a running browser, pick a
design action, and hot-swap AI-generated HTML+CSS variants via the dev server's HMR.

**This mode is not available in the dependency-free vendored skill.** It is built entirely
on impeccable's Node live-server machinery (`live.mjs`, `live-poll.mjs`, `live-wrap.mjs`,
`live-accept.mjs`, …), which is intentionally **not** vendored into this template — the
template stays language-agnostic with no runtime artifacts.

To use live mode, install impeccable's CLI directly and run it from your project:

```bash
npx impeccable skills install   # installs the full skill + its Node runtime
# then, in your tool: /impeccable live
```

See the upstream project for the full live-mode contract and setup:
https://github.com/pbakaus/impeccable

## What you can still do without it

Every other impeccable command works here without the CLI. For visual iteration without
live mode:

- Make a change, screenshot the rendered page, and compare against the General rules and
  Absolute bans in the parent skill.
- Use `/impeccable critique` and `/impeccable polish` for structured review-and-refine
  passes (both run fully manually in this dependency-free build).
- Use `/impeccable shape` to plan variants up front, then implement the chosen direction
  directly.
