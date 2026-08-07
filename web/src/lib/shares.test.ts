import { allocate, splitExpenseShares } from './shares'

const paidFor = (...shares: [string, number][]) =>
  shares.map(([id, share]) => ({ participant: { id }, shares: share }))

const sum = (values: number[]) => values.reduce((total, v) => total + v, 0)

describe('allocate', () => {
  it('spreads a remainder that cannot divide evenly', () => {
    expect(allocate(1000, [1, 1, 1])).toEqual([334, 333, 333])
  })

  it('always sums back to the total', () => {
    for (const total of [1, 7, 99, 1000, 12345]) {
      for (const weights of [
        [1, 1],
        [1, 1, 1],
        [1, 2, 7],
        [5, 5, 5, 5, 5, 5],
      ]) {
        expect(sum(allocate(total, weights))).toBe(total)
      }
    }
  })

  it('keeps negative totals negative and still exact', () => {
    const parts = allocate(-1000, [1, 1, 1])
    expect(sum(parts)).toBe(-1000)
    expect(parts.every((part) => part <= 0)).toBe(true)
  })

  it('returns zeroes when there is nothing to weigh by', () => {
    expect(allocate(500, [0, 0])).toEqual([0, 0])
    expect(allocate(500, [])).toEqual([])
  })
})

describe('splitExpenseShares', () => {
  it('splits evenly, cents included', () => {
    expect(
      splitExpenseShares({
        amount: 1000,
        splitMode: 'EVENLY',
        paidFor: paidFor(['a', 1], ['b', 1], ['c', 1]),
      }),
    ).toEqual([
      { participantId: 'a', amount: 334 },
      { participantId: 'b', amount: 333 },
      { participantId: 'c', amount: 333 },
    ])
  })

  it('ignores the stored shares in EVENLY mode', () => {
    expect(
      splitExpenseShares({
        amount: 900,
        splitMode: 'EVENLY',
        paidFor: paidFor(['a', 5], ['b', 1]),
      }),
    ).toEqual([
      { participantId: 'a', amount: 450 },
      { participantId: 'b', amount: 450 },
    ])
  })

  it('weighs BY_SHARES', () => {
    expect(
      splitExpenseShares({
        amount: 3000,
        splitMode: 'BY_SHARES',
        paidFor: paidFor(['a', 2], ['b', 1]),
      }),
    ).toEqual([
      { participantId: 'a', amount: 2000 },
      { participantId: 'b', amount: 1000 },
    ])
  })

  it('weighs BY_PERCENTAGE and still sums to the total', () => {
    const shares = splitExpenseShares({
      amount: 10000,
      splitMode: 'BY_PERCENTAGE',
      // percentages are stored out of 10000
      paidFor: paidFor(['a', 3333], ['b', 3333], ['c', 3334]),
    })
    expect(sum(shares.map((share) => share.amount))).toBe(10000)
  })

  it('takes BY_AMOUNT shares as they are', () => {
    expect(
      splitExpenseShares({
        amount: 1000,
        splitMode: 'BY_AMOUNT',
        paidFor: paidFor(['a', 700], ['b', 300]),
      }),
    ).toEqual([
      { participantId: 'a', amount: 700 },
      { participantId: 'b', amount: 300 },
    ])
  })

  it('handles an expense nobody was paid for', () => {
    expect(
      splitExpenseShares({ amount: 1000, splitMode: 'EVENLY', paidFor: [] }),
    ).toEqual([])
  })
})
