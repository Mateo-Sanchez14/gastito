# gastito 🧾

Gastos compartidos entre amigos, cargados por **mensajes de WhatsApp en lenguaje
natural**, con tracking multi-moneda en USD.

Mandás al grupo de WhatsApp algo como _"pagué 15 lucas de birra anoche, dividido
entre todos"_ y el bot lo interpreta, convierte ARS→USD según la cotización
elegida, y carga el gasto. Después ves todo (gastos, saldos, quién le debe a
quién) en la web.

## Arquitectura

```
WhatsApp grupo
     │  (mensajes)
     ▼
   Gowa  ──webhook (HMAC)──▶  bot (FastAPI + Claude + FX)
   ▲                              │
   └────── responde ◀────────────┤ POST /api/bot/* (bearer)
                                  ▼
                              web (spliit)  ──▶  Postgres
```

- **`web/`** — [spliit](https://github.com/spliit-app/spliit) vendorizado (Next.js
  + Prisma + tRPC): UI, saldos, splits, multi-moneda. Le agregamos endpoints
  `/api/bot/*` para que el bot cargue/consulte gastos. Ver [VENDORED.md](VENDORED.md).
- **`bot/`** — servicio Python que recibe los mensajes de Gowa, los parsea con
  un LLM (GitHub Models `openai/gpt-4o-mini` como primario, Gemini de fallback),
  convierte la moneda y llama a la web.
- **`gowa`** — [go-whatsapp-web-multidevice](https://github.com/aldinokemal/go-whatsapp-web-multidevice),
  transporte (no oficial) de WhatsApp.
- **`postgres`** — base de datos.

## Cómo correrlo

1. `cp .env.example .env` y completá los secretos (`GITHUB_MODELS_TOKEN` y
   opcionalmente `GEMINI_API_KEY` para el parseo; `BOT_INGEST_SECRET` y
   `GOWA_WEBHOOK_SECRET` pueden ser cualquier string fuerte).
2. `docker compose up --build`
3. **Parear WhatsApp:** abrí http://localhost:4000 (user/pass = `GOWA_BASIC_AUTH_*`),
   escaneá el QR con el WhatsApp del bot (idealmente un número dedicado).
4. **Crear el grupo en la web:** abrí http://localhost:3000, creá un grupo (ej.
   "Asado") con sus participantes, moneda **USD**. Copiá el id del grupo de la URL.
5. **Vincular el grupo de WhatsApp** al grupo de spliit (una vez). Necesitás el
   `chatId` del grupo de WhatsApp (`...@g.us`) — mandá cualquier mensaje al grupo
   y miralo en los logs del bot (`docker compose logs -f bot`), después:
   ```bash
   curl -X POST http://localhost:3000/api/bot/link \
     -H "Authorization: Bearer $BOT_INGEST_SECRET" \
     -H "Content-Type: application/json" \
     -d '{"chatId":"<...@g.us>","groupId":"<group-id>","fxArsSource":"blue"}'
   ```
6. **Presentarse:** cada integrante manda en el grupo `/soy <su nombre>` (el mismo
   nombre que el participante en la web).
7. ¡Listo! Mandá _"pagué 15 lucas de birra, entre todos"_, confirmá con *sí* y miralo aparecer en la web.

## Comandos del bot

| Comando | Qué hace |
|---|---|
| `/soy <nombre>` | Vincula tu WhatsApp a tu participante del grupo |
| `saldo` | Muestra quién le debe a quién |
| `resumen` | Cuánto puso y cuánto gastó cada uno (+ saldo neto) |
| `deshacer` | Borra tu último gasto |
| `/cotizacion oficial\|blue\|mep` | Elige qué dólar usar para convertir ARS |
| `ayuda` | Ayuda |

### Registrar un gasto (con confirmación)

Cuando contás un gasto, el bot **no lo guarda de una**: te muestra cómo quedaría
(_"📝 Voy a registrar: …"_) y lo registra recién cuando respondés *sí*. Con *no*
lo descarta. Si no aclaraste de qué fue, primero te pide una descripción. Una vez
confirmado, el bot no manda otro mensaje: solo reacciona con ✅ al *sí*. (Si lo
pagó Pichi, la confirmación lo marca como sospechoso 👀.)

### Editar un gasto

Para corregir un gasto, **respondé (citá) tu mensaje original del gasto** (o el
`✏️ Actualizado: …` de una corrección previa) y escribí la corrección en lenguaje
natural — el bot usa el mensaje citado como contexto para saber qué gasto editar.
Ejemplos:

- _"eran 8000 no 800"_ — corrige el monto
- _"en realidad pagó Benja"_ — cambia quién pagó
- _"dividí entre todos menos Pichi"_ — cambia entre quiénes se divide

El bot reconstruye el gasto completo (manteniendo lo que no cambiás), actualiza
spliit y confirma con `✏️ Actualizado: …`. Cualquier miembro del grupo puede editar.

## Deploy

Corre en el droplet `root@msanchez.me`, integrado al deployment-server: en
`/srv/gastito`, sobre `shared-postgres` y el Caddy compartido (`caddy_net`).
Subdominios: `gastito.msanchez.me` (web) y `gastito-wa.msanchez.me` (pareo Gowa).

**Auto-deploy:** push/merge a `main` dispara un webhook firmado a
`https://deploy.msanchez.me/webhooks/github/gastito`, que hace `git fetch/reset`
+ `docker compose -f docker-compose.prod.yml --env-file .env.production build &&
up -d`. (Reconstruye en el droplet de 1GB, ~9 min, usa swap.)

**Deploy manual (si hace falta):**
```
ssh root@msanchez.me 'cd /srv/gastito && git pull && \
  docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build'
```

## Notas

- **Cotización ARS:** configurable por grupo (oficial/blue/mep vía dolarapi.com).
  Cada confirmación muestra la cotización usada. Oficial vs blue puede ~duplicar
  el valor — acordalo en el grupo.
- **Gowa es no oficial** (riesgo de ban de WhatsApp). Usá un número dedicado.
- **Login de la web:** la UI está detrás de un usuario/contraseña compartido
  (Basic Auth), uno solo para todo el grupo. Se setea con `WEB_BASIC_AUTH_USER`
  y `WEB_BASIC_AUTH_PASS`; si los dejás vacíos no pide login (útil en local). El
  navegador lo pide una vez y lo recuerda. Para cambiar la clave editás el env y
  recreás el contenedor (`docker compose ... up -d web`) — se lee en runtime, no
  hace falta rebuild. Los endpoints del bot (`/api/bot/*`) y los health checks
  quedan exentos (tienen su propia auth).
- **Auth liviana, no fuerte:** cualquiera con el login de la web —o que esté en
  el grupo de WhatsApp— puede cargar gastos. Pensado para un grupo de amigos de
  confianza, no para datos sensibles.
- La base puede ser Supabase u otro Postgres: sólo cambiá el connection string en
  `docker-compose.yml` (`POSTGRES_PRISMA_URL` / `POSTGRES_URL_NON_POOLING`).
