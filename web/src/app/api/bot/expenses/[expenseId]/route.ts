import { deleteExpense, getExpense, updateExpense } from '@/lib/api'
import {
  BotExpensePayload,
  buildExpenseFormValues,
  requireBotAuth,
  validateBotSplit,
} from '@/lib/bot-auth'

// gastito: delete an expense (the `deshacer`/undo command). groupId is required
// as a query param so we can scope + log the activity correctly.
export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ expenseId: string }> },
) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const { expenseId } = await ctx.params
  const url = new URL(req.url)
  const groupId = url.searchParams.get('groupId')
  const participantId = url.searchParams.get('participantId') ?? undefined

  if (!groupId) {
    return Response.json({ error: 'groupId query param is required' }, { status: 400 })
  }

  const expense = await getExpense(groupId, expenseId)
  if (!expense || expense.groupId !== groupId) {
    return Response.json({ error: 'expense not found in group' }, { status: 404 })
  }

  await deleteExpense(groupId, expenseId, participantId)
  return Response.json({ deleted: { id: expenseId, title: expense.title } })
}

// gastito: edit an existing expense (the reply-to-edit flow). The bot has
// already resolved the *full* new state (participant ids + group-currency
// cents), so we rebuild the form values and replace the expense wholesale.
export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ expenseId: string }> },
) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const { expenseId } = await ctx.params

  let payload: BotExpensePayload & { createdByParticipantId?: string }
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

  const existing = await getExpense(payload.groupId, expenseId)
  if (!existing || existing.groupId !== payload.groupId) {
    return Response.json({ error: 'expense not found in group' }, { status: 404 })
  }

  try {
    const formValues = buildExpenseFormValues(payload)
    const expense = await updateExpense(
      payload.groupId,
      expenseId,
      formValues,
      payload.createdByParticipantId,
    )
    return Response.json({ expense }, { status: 200 })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return Response.json({ error: message }, { status: 400 })
  }
}
