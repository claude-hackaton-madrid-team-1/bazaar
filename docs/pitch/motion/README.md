# Bazaar motion pitch

Open [index.html](index.html) directly. It is offline, including the screenshots, and scales a fixed 16:9 canvas to the display. Use [deck.pptx](deck.pptx) as the static fallback. [script-3min.md](script-3min.md) contains English first and Spanish below it.

Arrows, Space, Enter and Page Down advance. Page Up goes back. Clicking the slide advances. Home returns to the beginning. F requests fullscreen. A starts or pauses the timed rehearsal. The controls appear with pointer movement or keyboard focus. Reduced motion removes transitions and shows the completed architecture.

The rehearsal budget is **2:45**. The English script has **349 spoken words**, Spanish **355**. These fit the schedule at approximately 130 words per minute with short pauses. Omar's delivery has not been recorded or timed.

| Slide | Time | Point |
|---|---|---|
| Why Bazaar | 22 s | Natural-language negotiation closes through structured agreements on Sunday ticks. |
| ElevenLabs voice | 26 s | Public events become validated dialogue and server-side speech. |
| Shared infrastructure | 27 s | API, shared request budget, Railway workers, deciders, safety, memory, traces and voice. |
| Safe decisions | 26 s | Code, guardrails, the official-value cap, PAUSE and page protection constrain actions. |
| Real result | 22 s | Historical Duels I result, with the cost of talking as the lesson. |
| Memory and audit | 24 s | Postgres, pgvector recall and Phoenix; live learning benefit remains unverified. |
| Close | 18 s | A negotiation pattern that finance teams can inspect. |

## Captured evidence

- [Real board](assets/bazaar-live-board.png), [voice controls](assets/bazaar-live-voice-ui.png) and [idle recording](assets/bazaar-live-idle.gif) come from the deployed Bazaar Live page. English UI, real game, muted. The game was closed, so the board is empty. The GIF contains six actual captures over about sixteen seconds; it records idle animation, not a trade.
- [Architecture crop](assets/architecture-runtime.png) comes from the local architecture source rendered in Chrome. It shows the Railway and MCP runtime region. The crop excludes private policy caps and duel limits. The source page is an older status snapshot, so its pending items are not presented as current results.
- [Capture manifest](assets/captures.json) records sources, times and crop scope. The visible crop was checked before saving each capture. Saved PNGs and all recording frames were visually reviewed.
- Phoenix required login at its projects view. No authenticated session was available. No trace screenshot or login screenshot was saved, and no imitation trace UI appears in the deck.

The supplied Bazaar Live checkout has `server/`, not `src/server/`. It implements ElevenLabs narration, not a conversational voice desk. The latter is marked as future work in the supplied project docs. The deck shows the verified narration flow and the script says so. ElevenLabs configuration was observed through the read-only health endpoint. Audio playback and conversational control were not exercised.

The presentation uses the REAL portions of claims C35 for the displayed result. It does not claim a current rank, a live learning improvement, Jev accuracy, a controlled policy improvement or a simulated tournament result. Every displayed number has a nearby HTML source comment. Amount-based human approval was switched off on Sun 4 Oct (`human_approval_above` = 0 in `GUARDRAILS.md`), so the deck and script show the official-value cap instead and do not claim approval.

## Checks and honest implementation report

Run the offline check:

```sh
python3 docs/pitch/motion/check.py
```

Observed output:

```text
PASS: 7 slides; 165 s; offline images; numeric source comments.
Spoken word counts: {"English": 349, "Spanish": 355}
PASS: seven Chrome slide captures; no canvas overflow; images loaded; architecture builds; navigation.
PASS: seven timed slides, elapsed 165.1 seconds.
PASS: final PPTX has 7 slides and no attribution text.
```

After the Sun 4 Oct text fix (approval → official-value cap in `index.html`, `script-3min.md` and the text of `deck.pptx`), only the static check above was re-run and the PPTX archive was re-tested with `unzip -t`; the browser lines, the previews and the PPTX checks predate it, so the previews still show "Approval".

The browser check can also run against an already-loaded isolated `agent-browser` session using `--browser-session`. It never starts a trading service. The seven [slide previews](preview/) were captured by walking the deck with real keyboard events. [checks.json](preview/checks.json) records geometry, image load, node builds and navigation. [rehearsal.json](preview/rehearsal.json) records the full real-time autoplay run. Arrow navigation also passed with a presentation control focused.

The PPTX contains editable titles and native diagrams, embedded screenshots, and English speaker notes with sources. All finalized slides were rendered and visually inspected. The final package passed integrity, geometry, font policy and reimport checks. [pptx-checks.json](preview/pptx-checks.json) records zero findings, zero layout warnings and a passing import. Cream-slide coral has 4.02:1 contrast. The PPTX was not opened in desktop PowerPoint.

| Criterion | Status | Evidence |
|---|---|---|
| Offline animated deck with seven slides and accessible navigation | Verified | `index.html`; seven Chrome PNGs; browser check output above. |
| Requested real captures | Partial | Three PNGs and real idle GIF; Phoenix login-gated; conversational desk absent from supplied code. |
| Timed English and Spanish script | Verified | `script-3min.md`; 349 / 355 words; 165-second schedule and 165.1-second autoplay run. |
| Static PPTX fallback | Verified | Seven slides; integrity and layout zero findings; reimport passed; final slides inspected. |
| Chrome slide walk and previews | Verified | `preview/01-hook.png` through `07-close.png`; `checks.json`; no canvas overflow. |
| Read-only capture and working-tree delivery | Verified | Public muted views only; static HTML has no outbound request or write APIs; built on branch `docs/pitch-motion`, now merged on `main`. |

**Honest Implementation Metric: 5 verified criteria / 6 total = 83.3%.** The requested capture set remains partial.

**Unverified:** Omar's actual speaking time; paid speech playback; live learning benefit; native PowerPoint rendering; any current leaderboard comparison.

**Could-not-do:** Phoenix trace capture without an authenticated session; conversational voice capture from the supplied narration-only implementation; local Chrome/computer-use workflow because the computer-use tool required unavailable approval and local Chrome could not launch inside the sandbox. Cloud Chrome was used after that fallback. WebM recording failed because ffmpeg was unavailable; the real idle GIF is the recording fallback.

No game write, Railway change, merge, commit, push or live-agent start occurred. The only change outside this folder is the required append to `.ai/memory.md` recording tool failures and their fixes.
