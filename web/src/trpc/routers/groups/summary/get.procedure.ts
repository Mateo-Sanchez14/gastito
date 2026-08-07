import { getGroupExpenses } from '@/lib/api'
import { splitExpenseShares } from '@/lib/shares'
import { baseProcedure } from '@/trpc/init'
import { z } from 'zod'

/**
 * Everything the summary tab needs, in one round trip: who was included in each
 * expense and for how much, plus the per-participant totals those add up to.
 *
 * Reimbursements are left out — they settle debts rather than add spending, and
 * including them would make the participant totals disagree with the stats tab.
 */
export const getGroupSummaryProcedure = baseProcedure
  .input(z.object({ groupId: z.string().min(1) }))
  .query(async ({ input: { groupId } }) => {
    const expenses = (await getGroupExpenses(groupId)).filter(
      (expense) => !expense.isReimbursement,
    )

    const participants = new Map<
      string,
      {
        id: string
        name: string
        /** What they owe across every expense they were included in. */
        share: number
        /** What they actually put on the table. */
        paid: number
        expenseCount: number
      }
    >()

    const track = (participant: { id: string; name: string }) => {
      const existing = participants.get(participant.id)
      if (existing) return existing
      const created = {
        id: participant.id,
        name: participant.name,
        share: 0,
        paid: 0,
        expenseCount: 0,
      }
      participants.set(participant.id, created)
      return created
    }

    const summarized = expenses.map((expense) => {
      track(expense.paidBy).paid += expense.amount

      const shares = splitExpenseShares(expense)
      const byParticipant = new Map(
        shares.map((share) => [share.participantId, share.amount]),
      )

      const paidFor = expense.paidFor.map(({ participant }) => {
        const amount = byParticipant.get(participant.id) ?? 0
        const tracked = track(participant)
        tracked.share += amount
        tracked.expenseCount += 1
        return { id: participant.id, name: participant.name, amount }
      })

      return {
        id: expense.id,
        title: expense.title,
        expenseDate: new Date(expense.expenseDate),
        amount: expense.amount,
        category: expense.category,
        paidBy: expense.paidBy,
        paidFor,
      }
    })

    return {
      expenses: summarized,
      participants: Array.from(participants.values()),
      total: expenses.reduce((sum, expense) => sum + expense.amount, 0),
      expenseCount: expenses.length,
    }
  })
