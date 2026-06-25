import { getGroup, getGroupExpenses } from '@/lib/api'
import {
  getBalances,
  getSuggestedReimbursements,
} from '@/lib/balances'
import { requireBotAuth } from '@/lib/bot-auth'

// gastito: balances + suggested reimbursements for the `saldo` command.
// Reuses spliit's own balance math so the WhatsApp answer matches the UI.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ groupId: string }> },
) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const { groupId } = await ctx.params
  const group = await getGroup(groupId)
  if (!group) {
    return Response.json({ error: 'unknown groupId' }, { status: 404 })
  }

  const expenses = await getGroupExpenses(groupId)
  const balances = getBalances(expenses)
  const reimbursements = getSuggestedReimbursements(balances)
  const nameById = Object.fromEntries(group.participants.map((p) => [p.id, p.name]))

  return Response.json({
    group: { id: group.id, name: group.name, currencyCode: group.currencyCode },
    // amounts are in group-currency cents
    balances: Object.entries(balances).map(([participantId, b]) => ({
      participantId,
      name: nameById[participantId] ?? participantId,
      ...b,
    })),
    reimbursements: reimbursements.map((r) => ({
      from: r.from,
      fromName: nameById[r.from] ?? r.from,
      to: r.to,
      toName: nameById[r.to] ?? r.to,
      amount: r.amount,
    })),
  })
}
