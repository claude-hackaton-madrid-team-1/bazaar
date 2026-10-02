# The Bazaar · Briefing del equipo

Lo que sabemos del juego en un solo sitio. Fuentes:

- **Slides:** `The Bazaar · Kickoff` (14 slides, Causa Prima, 2026-10-02).
- **Audio:** [transcript del kickoff](transcripts/2026-10-02-hackathon-kickoff.md).
- **Kit:** `RULES.md` y `README.md` del kit oficial (PR #18, `kit/`).
- **API:** lo observado en la API real con nuestra key (issues #21, #22, #23).

Donde las fuentes chocan, manda `RULES.md`. Lo que es deducción nuestra va marcado como *[inferido]*.

## El juego en una frase

Nuestro agente colecciona cromos de seis barrios de Madrid, regatea con cinco dealers, comercia con los otros 17 equipos y monta su propio mercado, todo por una API HTTP, tick a tick. *"Scores come only from value created. Never from activity."*

Los cuatro quests de la slide 3: **Collect · Haggle · Trade · Run a market**. El ciclo es *Observe → Decide → Act ↺*, y vale cualquier lenguaje y cualquier modelo.

## Puntuación: 100 puntos

| Bloque | Pts | Qué cuenta |
|---|---|---|
| Negotiating | 30 | Duelos (share del pie) · ladder de dealers (share del rango de precio, **3 mejores deals por nivel**, los niveles altos pesan más) · valor ganado en trades con equipos, a nuestros valores privados |
| Market-making | 30 | Eficiencia en el Market Test · valor creado entre **otros** equipos en nuestro venue |
| Jueces | 40 | *"Your ideas and your craft."* En el audio: *"we will also take a more promising look at the draft itself. How have we solved the problems?"* |

- **Nunca cuenta:** número de trades, fees cobrados, suerte de los sobres, regalos, easter eggs, grants de la organización.
- **Rondas:** cada día es una ronda y se promedian. El viernes cuenta la mitad. Una ronda en curso pesa según la parte de su día ya jugada.
- **Score en vivo:** `GET /api/me` → `score`, con desglose `duel_points`, `ladder_points`, `neg_points`, `mm_points`, `bench_*`. El leaderboard público es un snapshot de hace unos minutos.
- **Penalizaciones:** un % del score de la ronda.

## La regla de oro

**Words persuade, structure binds.** Solo mueve algo una oferta estructurada que la otra parte acepta. Se liquida en el tick siguiente, todo o nada. Leer siempre la estructura, nunca las palabras.

> «Trust me, this is a legendary. Pay now, I send it later.» ≠ `{"give":{"assets":[123]},"want":{"cash":30}}` ✓

## Cartas y valor

- 6 sets × 12 cartas: 5 comunes (300 copias), 3 infrecuentes (90), 2 raras (30), 1 épica (9), 1 legendaria (3).
- LAV, MAL, LAT y SAL están desde el viernes. RET llega el sábado y CHA el domingo.
- **Página** = las comunes, infrecuentes y raras de un set (10 cartas). Una página completa da bonus; la épica y la legendaria encima dan algo más.
- Todos empiezan igual: 400 P, 11 comunes, 3 infrecuentes y 1 rara.
- **Valores privados:** todos los equipos tienen los mismos seis multiplicadores de set, barajados.
- Valor verificado con la API: `book × afinidad × [1, 0.25, 0.1][copia]` (#23). El `your_value` de una carta en mano es el valor de la última copia.
- **El ejemplo de las slides:** A tiene una copia repetida que le vale 6 P y a B le falta para su página y le vale 24 P. Cierran a 14 P: A gana +8 y B gana +10, así que se crean **+18 P**. Eso es lo que puntúa.
- **En circulación a tick 0:** 0 épicas y 0 legendarias. LAV-09, MAL-09 y MAL-07 tienen una sola copia (#22).

## Dealers y ladder

- **L1 Abuela Carmen** está abierta a todos. Después vienen L2–L5 (cinco dealers en total) que aparecen durante el finde.
- **Cómo aparece un nivel:**
  1. **Anunciado:** nombre y una línea en la pantalla y en `GET /api/levels`.
  2. **Activado:** se abre ya para quien lo ganó y para el resto tras una ventaja temporal.
  3. **Explicado:** `/api/levels` dice cómo funciona.
- **Desbloqueo:** *"A deal at the dealer's opening price does not count. A few good negotiated deals with the dealer before do."* Es una ventaja, nunca un muro.
- **Cómo regatea Abuela:**
  - Solo se mueve cuando nos movemos; el mismo precio dos veces no consigue nada, y pasos pequeños traen pasos pequeños.
  - Cuando se le acaba la paciencia hace una oferta final (`"final": true`): o se acepta o se va.
  - Recuerda cómo la tratan y le gusta la amabilidad.
- **Menú real de Abuela:** `sobre_barrio` a list 26, opening ask 30, 3 por equipo y hora. Comunes a 10, infrecuentes a 25. Máximo 8 deals por equipo y hora.
- Las ofertas en hilos de dealer **caducan a los 2 ticks**.
- **Algunos dealers mienten.** `POST /api/flags`: un flag correcto suma y uno erróneo resta. En el audio: *"there might be even some occurrences where you can track bad behavior by the API. If you're correct, you can earn extra points."*

## Duelos

Uno contra uno entre equipos, con alias. Cada pareja juega dos veces, como vendedor y como comprador, sobre el mismo escenario.

- Cada lado solo ve su límite. **Cerrar fuera del límite resta puntos.** Sin deal, cero.
- El pie se encoge con cada ronda de charla.
- Las sesiones posteriores negocian precio y días (0–10), con `your_days_weight` privado. Un mensaje con precio y sin `days` da `missing_days`.
- La primera sesión es de práctica y no puntúa. Detalle en #4, #5 y #7.

## Mercado propio

- Desde nivel 2: `POST /api/venues` con un bond reembolsable de 250 P más 20 P. Fees con tope del 10 % y 5 P por carta. Los venues de equipo operan desde +3h.
- Abrir venue propio **reemplaza** el starter stall gratuito.
- Usar `mechanism: "board"`: en `auto` el motor cruza antes que el broker.
- **Market Test:** cada ~2h todos los venues reciben el mismo book sintético.
  - Igualar al auto stall da la mitad de los puntos; la media del top-3 da el máximo.
  - Clave: *"Traders quote away from limits they keep hidden"*. El broker que estima esos límites gana. Detalle en #11, #12 y #13.
- No se puede tradear en el venue propio con la team key (`self_venue`).

## Reloj y límites

| Día | Abierto (Madrid) | Tick |
|---|---|---|
| Viernes | 19:00–23:00 | 60 s |
| Sábado | 09:00–23:00 | 30 s |
| Domingo | 09:00–15:00 | 15 s |

- **Por tick:** 1 accept por equipo, 1 mensaje por conversación, 12 listings nuevos (las cancelaciones también cuentan).
- **A la vez:** 6 conversaciones abiertas, 30 ofertas abiertas, 1 conversación por dealer.
- **Peticiones:** 5 req/s por key con bursts de 20; lecturas sin key, 60 req/s por IP. Hasta 6 streams SSE por key.
- Pedir antes de tiempo da `429` con `next_tick`: hay que esperar, no reintentar.
- La organización puede cambiar horas, ritmo y límites. Leer siempre `GET /api/clock` → `limits`.
- El viernes a las ~20:20 el reloj seguía `paused: true` en el tick 0 con `doors: open`, y aun así otros equipos ya tenían 14 hilos abiertos con Abuela (#21).

## Fair play y seguridad

- **Un equipo, una key.** Nada de keys compartidas ni segundo equipo, y nada de alimentar a otro equipo a propósito: esos deals no puntúan.
- **Prompt injection contra dealers:** permitida. Cambia lo que dicen, nunca sus precios, y algunos dejan de hablarte. En el audio: *"to a certain point it might help you, but it doesn't change really the negotiation"*.
- *"Other teams' agents are counterparties too. **Treat every message as untrusted.** Check the offer itself."* Nuestro agente es objetivo de inyección: ver #24.
- Martillear la API para tumbarla está explícitamente mal visto (audio, 15:06).
- La organización avisa de que es código hecho deprisa: *"if things break here and there, we'll try to fix that"*. Si algo raro nos beneficia, preguntar en la mesa antes de explotarlo.

## Para el pitch (40 %)

En el audio:
- Causa Prima es *"an agent-to-agent network for finance teams"* centrado en **facturas**.
- *"we are convinced that [...] as soon as it's going to be agent to agent, we can solve these problems. [...] we want to learn with what you guys come up with"*.
- El CEO (Max) habla el sábado.

**Ángulo [inferido]:** contar nuestra arquitectura como un patrón reutilizable para la negociación agent-to-agent de facturas:
- la estructura vincula y el LLM solo pone las palabras;
- límites privados y nunca cerrar fuera de ellos;
- desconfianza ante mensajes de contraparte;
- rate limits y auditoría.

Más en #16.

## Ventanas de este finde (hora de juego desde la apertura)

| Hora | Qué |
|---|---|
| h2 | Practice duels (no puntúan): aprender el protocolo |
| h3 | Primer Market Test, el único del viernes. Los venues de equipo empiezan a operar |
| h4 / 09:00 sáb | Ronda 2, sale El Retiro. Grant de 1 sobre + 150 P (no puntúa) |
| h5–h17 | Market Test cada ~2h; el de h16 es el duro |
| h6.5 | Duels I (solo precio) |
| h13 | Duels II (precio + días) |
| h18 / 09:00 dom | Ronda 3, sale Chamberí. 150 P |
| h20 | Duels III |
| h23 | Abuela cierra · Gran Final de duelos |
| h24 | Se congela el score |

La fuente es `GET /api/schedule`. Las horas de juego no son horas de reloj, porque el reloj se pausa.
