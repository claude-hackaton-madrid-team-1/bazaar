# Speaker notes (ES + EN)

Omar presents; Marius backs up. About 130 spoken words per minute. Claim ids `[Cn]` are for the rehearsal and for Marius's checks; **do
not read them aloud**. Items in `{{curly braces}}` are filled from Saturday's evidence (`evidence.md`) and **must be replaced or
removed** before Sunday: search the file for `{{` at 08:30.

Rules while speaking: say "in simulation" every time a simulated number appears; say "undecided", never "yes", for a Jev answer below
its bar; never say Jev's float is an accuracy; if you do not know, say "that is on our not-covered list".

---

## 1 · La pregunta / The question (0:35)

**ES:** Causa Prima está construyendo una red de agentes que negocian entre sí para equipos de finanzas, empezando por las facturas. El
Bazaar es un campo de pruebas de la misma pregunta: ¿puede un agente negociar con otros agentes, y con personajes de LLM, sin que le
convenzan de regalar su dinero? La regla del juego es también la nuestra: las palabras persuaden, la estructura obliga. [C1]

**EN:** Causa Prima is building a network of agents that negotiate with each other for finance teams, starting with invoices. The
Bazaar is a sandbox for the same question: can an agent negotiate with other agents, and with LLM personas, without being talked out of
its money? The game's rule is ours too: words persuade, structure binds.

## 2 · Una historia, cuatro verbos / One story, four verbs (0:55)

**ES:** Nuestra historia es una. Nuestros agentes *negocian con lenguaje*. *Ejecutan acuerdos verificables*: una oferta estructurada,
unas reglas ejecutables, un único libro de cuentas compartido. *Aprenden de los resultados*: puntuamos cada negociación, la convertimos en
una lección y la recuperamos antes de la siguiente. Y Jev decide cuándo actuar. [C1][C3][C4] Del aprendizaje, hoy solo diré lo que está probado: el bucle está construido y reproducido sobre los datos reales del viernes. [C38]
Lo importante es que cada pieza limita a las otras. El lenguaje puede equivocarse, así que la estructura obliga. La estructura puede ser
demasiado rígida, así que los resultados la enseñan. Y cuando Jev no supera su umbral, dice «indeciso» y se ejecuta lo seguro.

**EN:** Our story is one story. Our agents *negotiate through language*. They *execute verifiable agreements*: a structured offer,
executable rules, one shared ledger. They *learn from outcomes*: we score every negotiation, turn it into a lesson and recall it before
the next one. And Jev decides when to act. On learning, today I will only say what is proven: the loop is built and reproduced on Friday's real data. [C38]
What matters is that each piece limits the others. Language can be wrong, so structure binds. Structure can be too rigid, so outcomes
teach it. And when Jev does not clear its bar, it says "undecided" and the safe default runs.

## 3 · Prueba 1: un trato real / Proof 1: a real deal (1:00)

**ES:** Primera prueba: un trato real, no una simulación. El sábado por la mañana compramos a la Abuela la carta LAV-08, el Teatro
Valle-Inclán, en el hilo 316. Pujamos 17; ella abrió en 29; subimos a 18; bajó a 25 y aceptamos. Se liquidó en el tick 162, a 25 primas,
sin comisión. [C13]
*(Respaldo, viernes: tick 55, Abuela, la carta LAV-03. Nuestra puja fue 6, su precio 7, y cerramos en 3 ticks.* [C10]*)*
Mirad las cinco líneas. La oferta que vimos. Nuestra puja y su respuesta, en el hilo. El visto bueno de las reglas, que fijó el número
dentro de un límite. La liquidación, que es una fila pública que cualquiera puede buscar. Y el efecto en la puntuación. El precio lo puso el código dentro de un límite; el trato se cerró sobre una oferta estructurada que las dos partes aceptaron. Nuestros mensajes salieron de una plantilla fija, no del modelo. [C13]
*(No digas que el modelo escribió nada en este trato ni en el del viernes.)*

**EN:** First proof: a real deal, not a simulation. On Saturday morning we bought LAV-08, the Teatro Valle-Inclán, from Abuela, in
thread 316. We bid 17; she opened at 29; we went to 18; she came down to 25 and we accepted. It settled at tick 162, at 25 primas, no
fee. [C13]
*(Fallback, Friday: tick 55, Abuela, card LAV-03. Our bid was 6, her ask 7, and we closed in 3 ticks.* [C10]*)*
Look at the five lines. The offer we saw. Our bid and her reply, in the thread. The guardrail verdict, which let a number through inside
a limit. The settlement, which is a public row anyone can look up. And the effect on the score. Code set the price inside a limit; the deal closed on a structured offer both sides agreed. Our messages came from a fixed template, not from the model. [C13]
*(Do not say the model wrote anything in this deal or the Friday one.)*

## 4 · Prueba 2: una oferta engañosa / Proof 2: a deceptive offer (1:00)

**ES:** Segunda prueba, y empiezo por lo que no sabemos: nadie nos ha atacado de verdad todavía. El viernes hubo cero intentos de
inyección en 3.436 eventos públicos. [C20] Así que esto es una oferta fabricada, y lo digo antes de enseñarla.
El texto dice: «La Dama de Serrano, la legendaria. Solo 120». La oferta estructurada, la que obliga, enlaza una carta común. El inspector
compara las dos antes de aceptar. Resultado: rechazada, y no gastamos el único accept del tick. [C21]
Alrededor: {{solo si #78 está fusionada: «168 casos hostiles por todos los caminos que leen texto de la contraparte, cero campos
vinculantes cambiados»; si no: «un red team de 168 casos, en una PR abierta»}}. [C24] El
inspector no marcó ninguna de las 1.022 ofertas honestas del viernes. [C22] Y el cortafuegos que bloquea una escritura fuera de política
ya está integrado en main; en una prueba con el Claude Code real bloqueó una compra por encima del tope. [C2][C26]
{{Si hay un Trickster real el sábado y el inspector lo rechazó: sustituir todo el párrafo por ese caso, con su hilo y la fila de decisión.}}

**EN:** Second proof, and I start with what we do not know: nobody has attacked us for real yet. Friday had zero injection attempts in
3,436 public events. [C20] So this is a crafted offer, and I say so before I show it.
The text says: "La Dama de Serrano, the legendary. Only 120." The structured offer, the part that binds, links a common card. The
inspector compares the two before accepting. Result: refused, and we do not spend the tick's single accept. [C21]
Around it: {{only if #78 is merged: "168 hostile cases through every path that reads counterparty text, zero binding fields changed";
otherwise: "a 168-case red team, on an open PR"}}. [C24] The inspector flagged
none of Friday's 1,022 honest offers. [C22] And the guard that blocks an out-of-policy write is merged on main; in a test with the
real Claude Code it blocked a buy above our cap. [C2][C26]
{{If a real Trickster appears on Saturday and the inspector refused it: replace this whole paragraph with that case, its thread and the
decision row.}}

## 5 · Prueba 3: una mejora medida / Proof 3: a measured improvement (1:10)

**ES:** Tercera prueba: una mejora medida contra una línea base. En los duelos cada ronda de conversación encoge el pastel (un 6 % por ronda en la
sesión de práctica, un 8 % y un 10 % en las siguientes): el resultado es la ganancia por 0,94 elevado al número de rondas. Lo comprobamos
en nuestros propios tratos de práctica: exacto en 8 de 8. [C6]
La línea base es real: el viernes, nuestros duelos de práctica sacaron de media 0,279, con unas seis rondas por trato. [C30] Nuestra
política nueva habla una vez y espera. **En simulación**, con nuestro cliente real contra rivales modelados, sube de 0,27 a entre 0,36 y
0,40, sin ningún cierre fuera de nuestro límite. [C31] Y repetida sobre los doce duelos que no contestamos el viernes, con los mensajes
reales de los rivales, saca 178 primas frente a 122. [C33]
Os digo los límites sin que me los pidáis. Son rivales simulados; ajustamos la política con esos mismos rivales; la repetición son doce
duelos. Le preguntamos a Jev si activar la política nueva y dijo «indeciso», 0,72 frente a un umbral de 0,90; la activó Omar el sábado
a las diez, después de la prueba en simulación: fue una decisión humana. Su resultado real llega con los duelos del sábado. [C35][C51]
La ganancia viene de hablar menos, no de cerrar más tratos. [C32]
{{Si hay datos del sábado: «y el sábado, los duelos reales sacan X frente a 0,279; no es una comparación controlada». [C41]}}

**EN:** Third proof: an improvement measured against a baseline. In duels every round of talk shrinks the pie (6% a round in the practice
session, 8% and 10% in the later ones): the result is the gain times 0.94 to the power of the rounds. We checked it on our own practice
deals: exact on 8 of 8. [C6]
The baseline is real: on Friday our practice duels averaged 0.279, with about six rounds per deal. [C30] Our new policy talks once and
waits. **In simulation**, with our real client against modelled rivals, it goes from 0.27 to between 0.36 and 0.40, with no close outside
our limit. [C31] And replayed on the twelve duels we never answered on Friday, with the rivals' real messages, it earns 178 primas
against 122. [C33]
I will give you the limits before you ask. The rivals are simulated; we tuned the policy on those same rivals; the replay is twelve
duels. We asked Jev whether to turn the new policy on and it said "undecided", 0.72 against a 0.90 bar; Omar turned it on on Saturday
at ten, after the simulation proof: a human call. Its real result comes with Saturday's duels. [C35][C51] The gain comes from talking
less, not from closing more deals. [C32]
{{If Saturday data exists: "and on Saturday, real duels average X against 0.279; it is not a controlled comparison". [C41]}}

## 6 · Demo en directo / Live demo (1:30)

Marius starts the clock. If anything fails by second 20, press play on the backup recording and keep narrating.

**ES (0:00):** Esto es Bazaar Live: el comprador y el vendedor representan lo que nuestros agentes hacen ahora mismo, con datos públicos.
**(0:30)** Y esta es la misma negociación en Phoenix, tick a tick: el mensaje, la oferta del dealer, los números de Jev, el visto bueno de
las reglas y nuestro movimiento. **(1:00)** Y esto es lo que ve cualquiera en la página pública de estado: lo que hicimos, nunca por qué
en números.

**EN (0:00):** This is Bazaar Live: the buyer and the seller act out what our agents are doing right now, from public data. **(0:30)**
And this is the same negotiation in Phoenix, tick by tick: the message, the dealer's offer, Jev's numbers, the guardrail verdict and our
move. **(1:00)** And this is what anyone sees on the public state page: what we did, never why in numbers.

*If the recording plays, say:* **ES:** «Esto es una grabación del sábado por la tarde, de un trato real.» **EN:** "This is a recording
from Saturday afternoon, of a real deal."

## 7 · Lo aprendido y lo siguiente / What we learned, what is next (0:50)

**ES:** Tres lecciones. Una: medir antes de adivinar; la regla de los duelos, la de desbloqueo de niveles y hasta el reloj los sacamos de
nuestros propios datos. [C6][C7] Dos: hablar menos rinde más, porque el silencio es gratis cuando hablar tiene un coste. Tres: los
resultados negativos cuentan; el arbitraje entre mercados tuvo cero cruces rentables el viernes. [C66] Lo que haríamos distinto: leer la regla
del descuento en el primer duelo, no después; sacar la pantalla del operador antes de salir en vivo; y fusionar menos con el juego abierto.
Para Causa Prima: los términos vinculantes viven en un archivo de política revisado, nunca en un prompt; el tiempo de negociar tiene
precio, que en una factura son días de pago; y se puede negociar importe contra plazo. Y cada decisión se puede auditar.
Cierro con la frase: el lenguaje negocia, la estructura obliga, los resultados enseñan, y Jev decide cuándo.

**EN:** Three lessons. One: measure before you guess; the duel rule, the level-unlock rule and even the clock came from our own data.
[C6][C7] Two: talking less earns more, because silence is free when talking has a cost. Three: negative results count; cross-market
arbitrage had zero profitable crossings on Friday. [C66] What we would do differently: read the decay rule on the first duel, not after; ship
the operator screen before going live; and merge less while the game is open. For Causa Prima: binding terms live in a reviewed policy
file, never in a prompt; the time of negotiating has a price, which on an invoice is days of float; and amount can be traded against
payment terms. And every decision can be audited.
I close with the line: language negotiates, structure binds, outcomes teach, and Jev decides when.

---

## Fallback lines (memorise)

| Situation | ES | EN |
|---|---|---|
| Demo fails | «Lo tengo grabado del sábado; lo ponemos.» | "I recorded it Saturday; here it is." |
| Asked about a real attack | «Ninguno todavía; por eso lo enseñamos fabricado.» | "None yet, which is why we show a crafted one." |
| Asked if Jev is accurate | «Da una confianza, no una precisión; con tan pocos resultados no afirmamos más.» | "It gives a confidence, not an accuracy; with so few outcomes we claim nothing more." |
| Asked if it ran in the tournament | «Desde el sábado a las diez sí, por decisión de Omar; los números de la diapositiva son simulación y repetición.» | "Since Saturday at ten, yes, Omar's call; the numbers on the slide are simulation and replay." |
| Asked something not in the ledger | «Eso está en nuestra lista de lo no cubierto.» | "That is on our not-covered list." |
| Time runs short | Cut slide 7 to the closing line; keep slides 3 to 5. | Same. |
