import { deleteExpense, getExpense } from '@/lib/api'
import { requireBotAuth } from '@/lib/bot-auth'

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
