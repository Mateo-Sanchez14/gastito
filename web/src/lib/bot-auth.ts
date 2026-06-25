/**
 * gastito: shared auth + payload helpers for the `/api/bot/*` route handlers.
 *
 * These endpoints are internal (bot -> web on the compose network), guarded by
 * a shared bearer secret. They are NOT meant to be public.
 */
import { ExpenseFormValues } from '@/lib/schemas'
import { SplitMode } from '@prisma/client'

/**
 * Fail-closed bearer check. Returns a 401 Response when the request is not
 * authorized, or `null` when it is OK to proceed.
 */
export function requireBotAuth(req: Request): Response | null {
  const secret = process.env.BOT_INGEST_SECRET
  if (!secret) {
    return Response.json(
      { error: 'BOT_INGEST_SECRET is not configured on the server' },
      { status: 503 },
    )
  }
  const header = req.headers.get('authorization') ?? ''
  const token = header.startsWith('Bearer ') ? header.slice(7).trim() : ''
  if (!token || token !== secret) {
    return Response.json({ error: 'unauthorized' }, { status: 401 })
  }
  return null
}

export type BotExpensePayload = {
  groupId: string
  title: string
  amount: number // group-currency (USD) cents
  category?: number
  paidById: string
  paidForIds: string[]
  shares?: number[]
  splitMode?: SplitMode
  originalAmount?: number
  originalCurrency?: string
  conversionRate?: number
  expenseDate?: string // ISO date
  notes?: string
  isReimbursement?: boolean
}

/**
 * Build the `ExpenseFormValues` that spliit's `createExpense` expects from the
 * bot's resolved payload. The bot has already resolved names -> participant ids
 * and converted money to group-currency cents, so we construct the
 * (already-transformed) form value directly rather than re-parsing.
 */
export function buildExpenseFormValues(
  payload: BotExpensePayload,
): ExpenseFormValues {
  const splitMode = payload.splitMode ?? 'EVENLY'
  const paidFor = payload.paidForIds.map((participant, i) => ({
    participant,
    shares: payload.shares?.[i] ?? 100, // matches spliit's "shares * 100" form convention
  }))

  return {
    expenseDate: payload.expenseDate ? new Date(payload.expenseDate) : new Date(),
    title: payload.title,
    category: payload.category ?? 0,
    amount: payload.amount,
    originalAmount: payload.originalAmount,
    originalCurrency: payload.originalCurrency ?? '',
    conversionRate: payload.conversionRate,
    paidBy: payload.paidById,
    paidFor,
    splitMode,
    saveDefaultSplittingOptions: false,
    isReimbursement: payload.isReimbursement ?? false,
    documents: [],
    notes: payload.notes,
    recurrenceRule: 'NONE',
  }
}
