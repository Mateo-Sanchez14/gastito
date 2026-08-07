import { getGroupExpenses } from '@/lib/api'
import { splitExpenseShares } from '@/lib/shares'
import { baseProcedure } from '@/trpc/init'
import { z } from 'zod'

/** What an expense without a seeded category falls back to. */
const UNCATEGORIZED = { id: 0, name: 'General', grouping: 'Uncategorized' }

export type CategorySlice = {
  id: number
  /** English DB name — the client uses it as the `Categories.*` i18n key. */
  name: string
  grouping: string
  /** Total spent on this category by the whole group, in minor units. */
  total: number
  /** What the active participant owes of that total, in minor units. */
  participantTotal: number
  expenseCount: number
  participantExpenseCount: number
}

/**
 * Spending per category, for the breakdown chart in the stats tab.
 *
 * Reimbursements are left out, the same way `getTotalGroupSpending` leaves them
 * out: they settle a debt, they are not spending.
 */
export const getGroupStatsByCategoryProcedure = baseProcedure
  .input(
    z.object({
      groupId: z.string().min(1),
      participantId: z.string().optional(),
    }),
  )
  .query(async ({ input: { groupId, participantId } }) => {
    const expenses = (await getGroupExpenses(groupId)).filter(
      (expense) => !expense.isReimbursement,
    )

    const byCategory = new Map<number, CategorySlice>()

    for (const expense of expenses) {
      const category = expense.category ?? UNCATEGORIZED
      const slice = byCategory.get(category.id) ?? {
        id: category.id,
        name: category.name,
        grouping: category.grouping,
        total: 0,
        participantTotal: 0,
        expenseCount: 0,
        participantExpenseCount: 0,
      }

      slice.total += expense.amount
      slice.expenseCount += 1

      if (participantId !== undefined) {
        const share = splitExpenseShares(expense).find(
          (share) => share.participantId === participantId,
        )
        if (share) {
          slice.participantTotal += share.amount
          slice.participantExpenseCount += 1
        }
      }

      byCategory.set(category.id, slice)
    }

    const categories = Array.from(byCategory.values())

    return {
      categories,
      total: categories.reduce((sum, slice) => sum + slice.total, 0),
      participantTotal: categories.reduce(
        (sum, slice) => sum + slice.participantTotal,
        0,
      ),
      expenseCount: expenses.length,
      participantExpenseCount: categories.reduce(
        (sum, slice) => sum + slice.participantExpenseCount,
        0,
      ),
    }
  })
