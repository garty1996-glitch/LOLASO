# Worlds LoL en Discord, sin servidor

Cada partida terminada del Worlds se publica en un canal de tu servidor con campeones, jugadores, KDA, bans y, para cada campeón, sus contadores acumulados: elegido 🎯, baneado 🚫 y winrate. Una vez al día añade un resumen de los más elegidos y más baneados. Datos de Leaguepedia.

Funciona con un **webhook de Discord** y **GitHub Actions** (gratis): no necesitas VPS, Raspberry ni dejar nada encendido.

## 1. Crear el webhook en Discord
1. En tu servidor: clic derecho en el canal → **Editar canal → Integraciones → Webhooks → Nuevo webhook**.
2. Ponle nombre (p. ej. "Worlds") y pulsa **Copiar URL del webhook**.

## 2. Subir el proyecto a GitHub
1. Crea un repositorio **público** en GitHub (en público, Actions es ilimitado y gratis; en privado se agotaría el cupo mensual gratuito a este ritmo).
2. Sube estos archivos tal cual, incluida la carpeta `.github/workflows/`: `worlds_webhook.py`, `.github/workflows/worlds.yml` y `README.md`.
3. En el repo: **Settings → Secrets and variables → Actions → New repository secret**
   - Nombre: `DISCORD_WEBHOOK_URL`
   - Valor: la URL del webhook. (Es un secreto: no la pongas en ningún archivo.)
4. Pestaña **Actions** → si te lo pide, habilita los workflows → **worlds-bot → Run workflow** para probarlo.

El primer arranque no publica las partidas ya jugadas, solo apunta cuáles hay. A partir de ahí se publican las nuevas. Para publicar también las anteriores, añade `BACKFILL: "true"` en la sección `env` del paso "Publicar partidas nuevas" del workflow, y quítalo después.

## Comportamiento
- Se ejecuta cada ~10 minutos (GitHub a veces tarda más; los avisos pueden llegar con algo de retraso).
- Lo ya publicado se guarda en `state.json`, que el propio workflow sube al repo.
- El resumen diario sale a las 08:00 de Madrid (hora de verano; cambia `SUMMARY_HOUR_UTC` si quieres otra).
- Si Leaguepedia tarda en subir los datos de una partida (o sus bans), el aviso llega con ese retraso.
- Si el nombre de la página del torneo cambia, ajusta `TOURNAMENT_PREFIX`.

## Qué se pierde frente al bot con comandos
Un webhook solo puede publicar mensajes, no responder a comandos, así que no hay `/champ`, `/top` ni `/bans`. Se sustituyen por el resumen diario. Si algún día quieres los comandos, existe la versión con bot (`bot.py`), que sí necesita estar siempre encendida.
