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
  convierte la moneda y llama a la web. Las **notas de voz** las transcribe con
  Gemini y después siguen exactamente el mismo camino que un mensaje de texto.
- **`gowa`** — [go-whatsapp-web-multidevice](https://github.com/aldinokemal/go-whatsapp-web-multidevice),
  transporte (no oficial) de WhatsApp.
- **`postgres`** — base de datos.

## Cómo correrlo

1. `cp .env.example .env` y completá los secretos (`LLM_PRIMARY_TOKEN` para el
   parseo — cualquier proveedor OpenAI-compatible, por defecto Groq; **`GEMINI_API_KEY` es obligatoria si querés notas de voz** — y además
   hace de fallback del parseo; `BOT_INGEST_SECRET` y `GOWA_WEBHOOK_SECRET` pueden
   ser cualquier string fuerte).
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
| `/apodo <apodo> = <participante>` | Anota un apodo, ej. `/apodo Tuco = Fer` (o varios: `/apodo Fer = Fernando, Tuco, Tuquina`) |
| `/entra <nombres>` | Marca que alguien está, ej. `/entra Fer, Juan` |
| `/sale <nombres>` | Marca que alguien ya no está (no lo borra) |
| `/quienes` | Muestra quién está y quién no |
| `saldo` | Muestra quién le debe a quién |
| `resumen` | Cuánto puso y cuánto gastó cada uno (+ saldo neto) |
| `deshacer` | Borra tu último gasto |
| `/cotizacion oficial\|blue\|mep` | Elige qué dólar usar para convertir ARS |
| `ayuda` | Ayuda |

> 🎙️ Todo esto también funciona por **nota de voz** — ver [Notas de voz](#notas-de-voz-).

### Apodos

Cada participante puede tener apodos que el bot reconoce al leer un gasto o al
resolver quién pagó. Con `/apodo Tuco = Fer` (funciona en cualquier orden), tanto
_"pagó Tuco"_ como _"dividido entre Tuco y yo"_ resuelven a **Fer**. Un apodo no
puede repetirse entre personas ni coincidir con el nombre real de otro participante.

> Nota: _"yo"_, _"mí"_, _"conmigo"_ se refieren a quien escribe. Si contás _"gasto
> entre Benja, Fer y yo"_, el split te incluye a vos automáticamente.

### Quiénes están

En un viaje el grupo está anotado entero desde el día uno, pero la gente llega y
se va en distintos momentos. Por eso, además de la lista de participantes, hay una
lista de **presentes**: es a quiénes alcanza un gasto _"entre todos"_.

```
/sale Pichi          → Pichi deja de contar en el "entre todos"
/entra Fer, Juan     → se suman (también sirve "/entra Fer y Juan")
/quienes             → ver la lista
```

Dos cosas importantes:

- **`/sale` no borra a nadie.** El participante sigue en el grupo con todos sus
  gastos y su saldo intactos, y sigue apareciendo en `saldo` y en `resumen`.
  (Borrarlo de verdad desde la web sí se lleva puestos los gastos que pagó.)
- **Nombrar a alguien siempre funciona**, esté o no. _"pagó Pichi"_ o _"entre Fer
  y Pichi"_ se registran igual aunque Pichi esté marcado como que no está —
  alguien que ya se volvió puede haber puesto plata igual.

El flag no es retroactivo: cada gasto congela entre quiénes se dividió en el
momento en que se cargó. En la config del grupo en la web, los que no están
figuran con un _"no está"_ al lado (se cambia solo por WhatsApp).

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

### Notas de voz 🎙️

Podés mandar un **audio** en lugar de escribir, y sirve para **todo** lo que hacés
por texto: contar un gasto nuevo, pasarle la descripción cuando te la pide,
responder *sí* / *no*, corregir un gasto citándolo, y los comandos (`saldo`,
`resumen`, `deshacer`, `/cotizacion`). El bot lo transcribe y desde ahí es
indistinguible de un mensaje escrito.

Reacciona con 👂 cuando escuchó el audio, y arranca su respuesta con lo que
entendió:

```
🎙️ Escuché: «pagué 8500 de birra anoche, entre todos»

Mateo, 📝 Voy a registrar: US$8.50 — birra (8500 ARS @ blue 1200)
Pagó Mateo, dividido entre todos.

¿Lo confirmo? Respondé sí o no.
```

**Ese eco no es decorativo.** La transcripción puede equivocarse (y con un audio
muy ruidoso o casi en silencio el modelo puede inventar), así que mostrarte lo que
escuchó *antes* de guardar es lo que te deja cazar el error a tiempo. Si algo salió
mal, respondé *no* y contalo de nuevo.

Detalles:

- Los números se transcriben en dígitos y la jerga queda como la dijiste:
  _"ocho mil quinientos"_ → `8500`, _"quince lucas"_ → `15 lucas`,
  _"dos palos y medio"_ → `2,5 palos`.
- El *sí* hablado puede ser una frase entera (_"sí, dale, confirmalo por favor"_).
- **Los comandos con barra conviene escribirlos.** `/soy`, `/apodo` y
  `/cotizacion` son incómodos de dictar: podés decir _"barra soy Mateo"_ y lo
  entiende, pero es más rápido tipear.
- Si el audio dura más de ~2 minutos o es un archivo reenviado (una canción, por
  ejemplo) el bot te avisa y no lo procesa.
- Si mandás un audio **con caption**, gana el caption y el audio no se transcribe.

#### Probar el camino de audio a mano

Para reproducir el flujo sin grabar un audio nuevo cada vez, se puede replayar un
webhook firmado apuntando a un `.ogg` que Gowa ya bajó
(`docker compose exec gowa ls statics/media`):

```bash
set -a; . ./.env; set +a
BODY='{"event":"message","payload":{"id":"FAKEAUDIO-001","chat_id":"<...@g.us>","from":"<sender>@s.whatsapp.net","from_name":"Mateo","timestamp":"1753617600","audio":"statics/media/<archivo>.ogg"}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$GOWA_WEBHOOK_SECRET" -r | cut -d' ' -f1)
curl -sS -X POST 'http://localhost:8000/webhooks/gowa/' -H 'Content-Type: application/json' -H "X-Hub-Signature-256: sha256=$SIG" --data-binary "$BODY"
```

Tres detalles que si no se respetan hacen perder un rato: `printf '%s'` (no `echo`,
que agrega `\n` y cambia el HMAC), `--data-binary` (no `-d`), y la **barra final**
en `/webhooks/gowa/`. Además el `chat_id` tiene que ser un grupo realmente
vinculado y el `from` un remitente ya mapeado con `/soy`, o el bot corta antes de
llegar a la transcripción.

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
- **Notas de voz:** el audio se manda a Gemini para transcribirlo — no queda
  guardado en la base, pero **la transcripción aparece en los logs del bot**
  (`docker compose logs bot`), que es la única forma de debuggear una mala
  transcripción. La transcripción es **Gemini-only**: `gpt-4o-mini` no escucha, así
  que a diferencia del parseo de texto acá **no hay proveedor de respaldo** — si
  Gemini se cae o te come el rate limit (~10-15 req/min en el tier gratis), los
  audios dejan de andar hasta que vuelva. Para apagarlos sin romper el parseo de
  texto: `VOICE_NOTES_ENABLED=false` (no alcanza con borrar `GEMINI_API_KEY`,
  porque esa key también es el fallback del extractor).
- **Los audios ocupan disco:** Gowa los baja a `gowa/statics/media/`, que crece con
  cada nota de voz. En el droplet conviene purgarlo cada tanto:
  ```bash
  find /srv/gastito/gowa/statics/media -type f -mtime +7 -delete
  ```
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
