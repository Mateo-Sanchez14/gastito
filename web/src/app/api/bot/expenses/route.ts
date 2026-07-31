import { createExpense, getGroup } from '@/lib/api'
import {
  BotExpensePayload,
  buildExpenseFormValues,
  requireBotAuth,
  validateBotSplit,
} from '@/lib/bot-auth'
import { getExpenseByExternalId } from '@/lib/bot'

// gastito: bot ingestion endpoint. Creates an expense from a payload the
// WhatsApp bot has already resolved (participant ids + group-currency cents).
// Idempotent on (source, externalId) so a Gowa webhook retry never double-books.
export async function POST(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  let payload: BotExpensePayload & {
    source?: string
    externalId?: string
    createdByParticipantId?: string
  }
  try {
    payload = (await req.json()) as typeof payload
  } catch {
    return Response.json({ error: 'invalid JSON body' }, { status: 400 })
  }

  if (!payload.groupId || !payload.title || !payload.paidById) {
    return Response.json(
      { error: 'groupId, title and paidById are required' },
      { status: 400 },
    )
  }
  if (!payload.paidForIds?.length) {
    return Response.json({ error: 'paidForIds must be non-empty' }, { status: 400 })
  }
  const splitError = validateBotSplit(payload)
  if (splitError) {
    return Response.json({ error: splitError }, { status: 400 })
  }

  const source = payload.source ?? 'whatsapp'
  const externalId = payload.externalId

  // Idempotency: short-circuit if this Gowa message already created an expense.
  if (externalId) {
    const existing = await getExpenseByExternalId(source, externalId)
    if (existing) {
      return Response.json(
        { expense: existing, idempotent: true },
        { status: 200 },
      )
    }
  }

  const group = await getGroup(payload.groupId)
  if (!group) {
    return Response.json({ error: 'unknown groupId' }, { status: 404 })
  }

  try {
    const formValues = buildExpenseFormValues(payload)
    const expense = await createExpense(
      formValues,
      payload.groupId,
      payload.createdByParticipantId,
      { source, externalId },
    )
    return Response.json({ expense, idempotent: false }, { status: 201 })
  } catch (err) {
    // A unique-violation here means a concurrent retry won the race; treat as ok.
    const message = err instanceof Error ? err.message : String(err)
    if (externalId && message.includes('Unique constraint')) {
      const existing = await getExpenseByExternalId(source, externalId)
      if (existing)
        return Response.json({ expense: existing, idempotent: true }, { status: 200 })
    }
    return Response.json({ error: message }, { status: 400 })
  }
}
