# Worlds LoL en Discord, sin servidor

Por cada partida terminada del Worlds se publican dos bloques en tu canal:

1. **La partida:** los 10 campeones elegidos y los 10 baneados. Cada uno lleva su acumulado en el Worlds contando esa partida (`Kled ×2` = elegido 2 veces en el torneo), y los elegidos también su winrate.
2. **Global del Worlds tras esta partida:** más elegidos, más baneados y **presencia del campeón** (veces elegido + veces baneado).

Datos de Leaguepedia. Funciona con un **webhook de Discord** y **GitHub Actions** (gratis): no necesitas VPS ni dejar nada encendido.

## Puesta en marcha
1. Webhook: en Discord, clic derecho en el canal → Editar canal → Integraciones → Webhooks → Nuevo webhook → Copiar URL.
2. Repo público en GitHub con `worlds_webhook.py`, `README.md` y `.github/workflows/worlds.yml`.
3. En el repo: Settings → Secrets and variables → Actions → New repository secret → `DISCORD_WEBHOOK_URL` con la URL del webhook.
4. Pestaña Actions → worlds-bot → Run workflow para probarlo.

## Actualizar el script
Abre `worlds_webhook.py` en el repo, pulsa el lápiz, selecciona todo, pega el contenido nuevo y haz Commit.

Las partidas ya publicadas están en `state.json` y no se repiten. Si quieres que se vuelvan a publicar con el formato nuevo, borra `state.json` y asegúrate de que el workflow tiene `BACKFILL: "true"`; después quita esa línea.

## Comportamiento
- Se ejecuta cada ~10 minutos (GitHub a veces tarda más).
- **Bans:** se leen de dos tablas de Leaguepedia. Si una partida aún no tiene bans cargados, el bot **espera** y no la publica hasta que estén (máximo `BANS_WAIT_HOURS`, 3 por defecto; pasado ese tiempo la publica con un aviso ⚠️). Las partidas siguientes esperan a esa para mantener el orden.
- Los nombres se normalizan (`Kai'Sa`, `KaiSa`...) para que el mismo campeón no se cuente dos veces.
- Opciones del workflow (variables `env`): `TOURNAMENT_PREFIX`, `BACKFILL`, `BANS_WAIT_HOURS`, `TOP_N` (10 por defecto).
- Para ver qué ha pasado en cada ejecución: Actions → la ejecución → paso "Publicar partidas nuevas". Ahí aparece cuántas partidas hay, cuántas tienen bans y si alguna está esperando.

## Limitaciones
- Un webhook no responde a comandos, así que no hay `/champ`, `/top` ni `/bans`.
- Si Leaguepedia tarda en subir una partida, el aviso llega con ese retraso.
