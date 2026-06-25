# Vendored dependencies

## spliit (`web/`)

- Upstream: https://github.com/spliit-app/spliit
- License: MIT
- Vendored commit: `d3b151e1506f26fd582c30b09427606cc2fe7826`
- Vendored on: 2026-06-25

We vendor (copy) spliit rather than use a submodule/fork because gastito makes
invasive changes (schema additions + new API route handlers). To pull an
upstream fix, diff against the pinned commit above and cherry-pick manually.

### gastito patches on top of spliit

- `prisma/schema.prisma`
  - `Expense`: added `source` + `externalId` columns and `@@unique([source, externalId])`
    (idempotency for Gowa webhook retries).
  - New models `WhatsAppGroupLink` (chatId -> Group, plus `fxArsSource`) and
    `WhatsAppMember` (senderJid -> Participant). Back-relations added to
    `Group.whatsappLinks` and `Participant.whatsappMembers`.
  - Migration: `prisma/migrations/20260625000000_add_whatsapp_bot/`
- `src/lib/api.ts` — `createExpense` gained an optional `provenance` arg
  (`{ source, externalId }`) passed through to the Prisma create.
- `src/lib/bot.ts` (new) — Prisma helpers for links/members/idempotency/undo.
- `src/lib/bot-auth.ts` (new) — bearer auth + payload->ExpenseFormValues builder.
- `src/app/api/bot/**` (new) — REST ingestion endpoints the WhatsApp bot calls:
  - `POST   /api/bot/expenses` (idempotent on source+externalId)
  - `DELETE /api/bot/expenses/[expenseId]?groupId=&participantId=`
  - `GET    /api/bot/groups/[groupId]/participants`
  - `GET    /api/bot/groups/[groupId]/balances`
  - `GET|POST|PATCH /api/bot/link`
  - `GET|POST /api/bot/members`
  - `GET    /api/bot/last-expense?groupId=&participantId=`

## Gowa (WhatsApp transport)

- Image: `aldinokemal2104/go-whatsapp-web-multidevice` (pulled, not vendored).
- Integration pattern (webhook HMAC, REST client) ported from the
  `american-store-chatbot` project into `bot/whatsapp/`.
