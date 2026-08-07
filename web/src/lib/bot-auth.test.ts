import { BotExpensePayload, validateBotSplit } from './bot-auth'

const base: BotExpensePayload = {
  groupId: 'g1',
  title: 'Starbucks',
  amount: 2990,
  paidById: 'p1',
  paidForIds: ['p1', 'p2', 'p3'],
}

describe('validateBotSplit', () => {
  it('accepts EVENLY without shares', () => {
    expect(validateBotSplit(base)).toBeNull()
    expect(validateBotSplit({ ...base, splitMode: 'EVENLY' })).toBeNull()
  })

  it('ignores BY_SHARES (bot never sends it)', () => {
    expect(validateBotSplit({ ...base, splitMode: 'BY_SHARES' })).toBeNull()
  })

  it('accepts a BY_AMOUNT split summing to amount', () => {
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_AMOUNT',
        shares: [696, 1067, 1227],
      }),
    ).toBeNull()
  })

  it('rejects BY_AMOUNT without shares', () => {
    expect(validateBotSplit({ ...base, splitMode: 'BY_AMOUNT' })).toMatch(
      /aligned/,
    )
  })

  it('rejects BY_AMOUNT shares misaligned with paidForIds', () => {
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_AMOUNT',
        shares: [696, 2294],
      }),
    ).toMatch(/aligned/)
  })

  it('rejects zero or negative or fractional shares', () => {
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_AMOUNT',
        shares: [0, 1763, 1227],
      }),
    ).toMatch(/positive integers/)
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_AMOUNT',
        shares: [-1, 1764, 1227],
      }),
    ).toMatch(/positive integers/)
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_AMOUNT',
        shares: [696.5, 1066.5, 1227],
      }),
    ).toMatch(/positive integers/)
  })

  it('rejects BY_AMOUNT shares not summing to amount', () => {
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_AMOUNT',
        shares: [696, 1067, 1228],
      }),
    ).toMatch(/must sum to 2990/)
  })

  it('accepts a BY_PERCENTAGE split summing to 10000 bp', () => {
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_PERCENTAGE',
        shares: [7000, 1500, 1500],
      }),
    ).toBeNull()
  })

  it('rejects BY_PERCENTAGE not summing to 10000 bp', () => {
    expect(
      validateBotSplit({
        ...base,
        splitMode: 'BY_PERCENTAGE',
        shares: [7000, 1500, 1000],
      }),
    ).toMatch(/must sum to 10000/)
  })
})
