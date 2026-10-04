# Bazaar motion pitch

Omar presents. Target duration: **2:45**, including transitions and pauses. Stop at 2:50.
Seven slides, timed 22 / 26 / 27 / 26 / 22 / 24 / 18 seconds. The HTML rehearsal uses the same timing.
Speak only one language. The Spanish version is an alternative, not an additional segment.

Sources and cues are not spoken. Screenshots are already embedded. No live commands are needed.

## English

### Why Bazaar · 0:00 to 0:22

At a Madrid flea market, every agent wants a good deal. A convincing sentence cannot be the contract. We built Bazaar to negotiate in natural language and close structured agreements. On Sunday's fifteen-second ticks, we must decide in time, with clear limits on money and cards.

Cue: point to the conversation becoming a structured agreement.
Sources: vendor rules, “Words persuade, structure binds” and “The clock”; claims C1.

### ElevenLabs voice · 0:22 to 0:48

This is our trading desk, made audible. Bazaar Live turns public events into buyer and seller dialogue. A server validates the lines, calls ElevenLabs, and returns audio. The key stays on the server. The capture is muted. Today we narrate trading activity. Conversational voice control is the next step.

Cue: show the real board and voice controls. The game was closed when captured.
Sources: bazaar-live README “Voices”; server/app.ts; server/providers.ts; src/tts/remote.ts. GET /health reported ElevenLabs configured, but playback was not exercised.

### Shared infrastructure · 0:48 to 1:15

Everything shares one team key and five requests per second. Railway runs the taker, maker, duels and MCP tools. Tick offsets reduce bursts. The deploy guard protects negotiation windows. Jev and model deciders advise within legal choices. Guardrails constrain execution. Postgres remembers, Phoenix traces, and Bazaar Live explains what happened.

Cue: let the architecture build. It is a system overview; the memory and trace systems also feed decisions, and voice observes public activity.
Sources: vendor rules “Fair play”; .railway/railway.py; README “Live services”, “Decider switch”, “Evals”; docs/services.md; GUARDRAILS.md deploy guard.

### Safe decisions · 1:15 to 1:41

We use models where they help: interpreting requests, advice and language. Code enforces every binding action. Jev can say undecided, and the safe default remains. A shared ledger coordinates accepts. Larger trades require human approval. PAUSE stops writes. We protect the last copy of a page card. Those limits live in inspectable policy and code.

Cue: point to the shield, then the four policy controls.
Sources: claims C1–C4; GUARDRAILS.md; AGENTS.md selling hard rules. The approval threshold is intentionally unnamed because the supplied policy and contract differ.

### Real result and lesson · 1:41 to 2:03

The lesson was that talking has a price. Each duel round shrinks the value available, so another counteroffer must earn its cost. In the real first scored duel session, we closed twenty-seven deals and recorded fifteen point zero two official duel points. That historical result does not prove a policy improvement.

Cue: pause on the REAL label. The shrinking ring illustrates the rule; it is not a measured chart.
Sources: claims C6 and C35, REAL portions; docs/briefing.md “Duels”. No simulator result or current leaderboard claim is shown.

### Memory and audit · 2:03 to 2:27

Outcomes go into Postgres. Hybrid recall with pgvector retrieves relevant lessons, and Phoenix traces let us inspect decisions. We reproduced the learning loop offline on real data. Its live benefit is unverified. Our practical lesson: build the operator view early, and deploy between negotiations. Visibility is part of running an agent.

Cue: follow the memory loop. The diagram is illustrative, not a Phoenix screenshot.
Sources: claims C38; docs/architecture.status.json; README “Evals”; docs/pitch/story.md “What we would do differently”.

### Close · 2:27 to 2:45

For Causa Prima, an invoice agent must negotiate amount and terms with clear authority. Bazaar gives us a working pattern: spoken activity, shared infrastructure, reviewed limits and an audit trail. Negotiation you can inspect. Thank you.

Cue: finish here. Leave the final slide up.
Source: kickoff framing in docs/pitch/story.md; verified architecture and policy sources above.

## Spanish

### Why Bazaar · 0:00 to 0:22

En un mercadillo de Madrid, cada agente busca un buen trato. Una frase convincente no puede ser el contrato. Construimos Bazaar para negociar en lenguaje natural y cerrar acuerdos estructurados. Con los ticks de quince segundos del domingo, decidimos a tiempo, con límites claros sobre dinero y cartas.

### ElevenLabs voice · 0:22 to 0:48

Esta es nuestra mesa de negociación, con voz. Bazaar Live convierte eventos públicos en diálogo entre comprador y vendedor. El servidor valida frases, llama a ElevenLabs y devuelve audio. La clave permanece en el servidor. La captura está silenciada. Hoy narramos la actividad. El control por conversación es el siguiente paso.

### Shared infrastructure · 0:48 to 1:15

Compartimos una clave de equipo y cinco peticiones por segundo. Railway ejecuta comprador, creador de mercado, duelos y herramientas MCP. Escalonamos los ticks y protegemos las negociaciones antes de desplegar. Jev y los modelos aconsejan entre opciones legales. Las reglas limitan la ejecución. Postgres recuerda, Phoenix registra y Bazaar Live explica lo ocurrido.

### Safe decisions · 1:15 to 1:41

Usamos modelos para interpretar peticiones, aconsejar y redactar. El código controla cada acción vinculante. Jev puede responder indeciso y seguimos con la opción segura. Un registro compartido coordina las aceptaciones. Los tratos grandes requieren aprobación humana. PAUSE detiene las escrituras. Protegemos la última copia de cada carta de página. Esos límites viven en código revisable.

### Real result and lesson · 1:41 to 2:03

Aprendimos que hablar tiene un precio. Cada ronda del duelo reduce el valor disponible, así que otra contraoferta debe compensar ese coste. En la primera sesión de duelos que puntuó, cerramos veintisiete acuerdos y registramos quince coma cero dos puntos oficiales. Ese resultado histórico no demuestra una mejora de política.

### Memory and audit · 2:03 to 2:27

Guardamos resultados en Postgres. La búsqueda híbrida con pgvector recupera lecciones y Phoenix permite revisar decisiones. Reprodujimos el aprendizaje sobre datos reales, fuera del juego. Su beneficio en vivo sigue sin verificar. Nuestra lección: construir pronto la pantalla del operador y desplegar entre negociaciones. La visibilidad forma parte del agente.

### Close · 2:27 to 2:45

Para Causa Prima, un agente de facturas debe negociar importe y plazos con autoridad clara. Bazaar ofrece un patrón que funciona: actividad con voz, infraestructura compartida, límites revisados y un registro auditable. Negociación que puedes inspeccionar. Gracias.

## Rehearsal controls

Open index.html. Arrows, Space, Enter and Page Down advance. Page Up goes back. Click the slide to advance. F requests fullscreen. A starts or pauses timed rehearsal from the current slide. Home restarts; End goes to the close. The controls appear when the pointer moves or a control receives focus. Reduced motion keeps the architecture fully visible and removes motion effects.

The durations are a rehearsal budget, not a measured recording of Omar. Leave a short pause where a segment finishes early. If behind, omit the last two sentences of the memory slide and keep the close.
