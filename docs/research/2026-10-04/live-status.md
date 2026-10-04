# Estado vivo — domingo 4 oct 2026 (Team 1)

> Tablero de hechos **verificados en vivo** para que nadie trabaje con datos viejos. No incluye
> valores privados nuestros (caja, valores de carta, caps, escalera de pujas): eso se redacta con
> `[private]` por convención del repo. Fuente: `/api/game/stream` (Bazaar Live), `/api/leaderboard`,
> `/api/clock` y `GUARDRAILS.md` en `origin/main`. Cada punto lleva la hora de comprobación.

## Resumen en una línea
Lo que nos tiene estáticos son **dos palancas apagadas**, no fallos de juego: los **duelos reinician
a 0 el domingo** (se rellenan en Duels III ~11:00 y la Final ~14:00) y la **escalera está atada por un
tope de gasto que el agente desplegado todavía aplica** pese al merge #281.

## Hechos verificados (10:48 Madrid)

1. **Tradear con dealers SÍ puntúa; la escalera NO ha muerto.**
   - `ladder_points` del domingo arrancó en 0 (la escalera reinicia cada ronda) y subió al comprar a
     dealers esta mañana. Prueba: nuestras compras CHA a Pícaros/Abuela movieron `ladder_points`.
   - Solo cuentan los **3 mejores tratos por nivel y dealer**; un 4.º trato en un nivel lleno suma 0.
   - **Dealers cierran a las 14:00.** Después, la escalera se congela para siempre.

2. **El agente desplegado NO recogió el merge #281.** Sigue aplicando `max_spend_per_game_hour = 250`
   y **frena compras buenas**. Visto en vivo: el maker rechazó una puja (valor por encima del precio)
   con `"spend 250 + ... > max_spend_per_game_hour 250"`, y el taker hizo `dealer_skip` por el mismo
   motivo. En `origin/main` el valor ya es 0 (sin tope). **Acción: reiniciar `bazaar-taker` y
   `bazaar-maker` en Railway** para que lo lean — pero NO durante duelos (ver timing).

3. **Jev está ACTIVO en los duelos y va bien.** Railway corre `duel run --play`, que lleva Jev por
   defecto. Los resultados reales de ayer con Jev fueron buenos (Duels I 79 % de tratos). Las
   simulaciones se midieron con `--no-jev`, pero **en real con Jev rinde**. **No cambiar.**

4. **`human_approval_above = 0` en main** (sin aprobación por importe), pero igual que el tope: solo
   aplica tras reiniciar los agentes.

5. **Mercado (30 %) puntúa los tratos ENTRE OTROS equipos en nuestro puesto (v19).** Vender en el
   puesto de un rival le da puntos de mercado a su dueño. Por eso importa la PR #276 (abajo).

## Timing crítico (reconfirmar con `/api/clock` antes de fusionar)
- **Fusionar cualquier cosa a `main` redespliega `bazaar-duels` y mata los duelos en curso.**
- **Duels III:** ~`t_hours` 15.367 ≈ **11:00**. No fusionar ni reiniciar nada mientras corre.
- **Ventana segura para merges/reinicios:** aprox. **11:15–12:25** (entre benches y duelos).
- **Dealers cierran + Final de duelos:** **14:00**.
- **Marcadores congelan:** **15:00**.

## PR abiertas relevantes
- **#276** `feat/maker-venue-avoid-rivals` — CI en verde, **abierta sin fusionar**. Dirige nuestras
  ventas al mejor comprador **que no sea rival** y al puesto que más nos puntúa, evitando los puestos
  de los equipos de cabeza (`buyer_rank_enabled`, `venue_avoid_rivals`, `venue_team_penalty = 0.5`).
  Importancia: deja de regalar puntos de mercado a rivales. **Fusionar en la ventana segura.**
- **#244** `fix/sa1-taller-hardening` — endurece el taller. Abierta.

## Qué NO hacer
- No vender copias únicas de páginas (protegidas): repetir el error SAL-07 resta mucho.
- No fusionar ni redeployar durante Duels III (~11:00) ni la Final (~14:00).
- No comprar en niveles de escalera ya llenos (3/3): suma 0.
