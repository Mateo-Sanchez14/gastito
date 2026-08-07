import { SplitMode } from '@prisma/client'

type ShareableExpense = {
  amount: number
  splitMode: SplitMode
  isReimbursement?: boolean
  paidFor: { participant: { id: string }; shares: number }[]
}

export type ParticipantShare = { participantId: string; amount: number }

/**
 * Distributes `total` across `weights` in whole minor units, so that the parts
 * always sum back to `total` exactly (largest-remainder method).
 *
 * `calculateShare()` in `totals.ts` returns floats, which is fine for a single
 * number on screen but drifts once you add up a column: an even split of 10.00
 * between 3 people renders as three times 3.33 under a 10.00 heading. The
 * summary view puts those numbers next to each other, so it needs them to add up.
 */
export function allocate(total: number, weights: number[]): number[] {
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0)
  if (weights.length === 0) return []
  if (totalWeight <= 0) return weights.map(() => 0)

  const sign = total < 0 ? -1 : 1
  const absolute = Math.abs(total)

  const exact = weights.map((weight) => (absolute * weight) / totalWeight)
  const parts = exact.map(Math.floor)
  const remainder = absolute - parts.reduce((sum, part) => sum + part, 0)

  // Hand the leftover units to the largest fractions first, ties by position so
  // the result is stable across renders.
  const byFraction = exact
    .map((value, index) => ({ index, fraction: value - Math.floor(value) }))
    .sort((a, b) => b.fraction - a.fraction || a.index - b.index)

  for (let i = 0; i < remainder; i++) parts[byFraction[i].index] += 1

  return parts.map((part) => part * sign)
}

/**
 * Splits an expense into what each participant it was paid for actually owes,
 * in minor units. The amounts sum exactly to `expense.amount`.
 */
export function splitExpenseShares(
  expense: ShareableExpense,
): ParticipantShare[] {
  const { paidFor } = expense
  if (paidFor.length === 0) return []

  // BY_AMOUNT stores the shares as the amounts themselves — nothing to spread.
  if (expense.splitMode === 'BY_AMOUNT') {
    return paidFor.map(({ participant, shares }) => ({
      participantId: participant.id,
      amount: Number(shares),
    }))
  }

  const weights = paidFor.map(({ shares }) =>
    expense.splitMode === 'EVENLY' ? 1 : Math.max(0, Number(shares)),
  )

  return allocate(expense.amount, weights).map((amount, index) => ({
    participantId: paidFor[index].participant.id,
    amount,
  }))
}
