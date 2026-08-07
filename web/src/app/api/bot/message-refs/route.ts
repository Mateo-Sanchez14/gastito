import {
  getBotExpenseDetail,
  getExpenseIdByMessage,
  recordMessageRef,
} from '@/lib/bot'
import { requireBotAuth } from '@/lib/bot-auth'

// gastito: WhatsApp message <-> expense links, backing the reply-to-edit flow.
//
// GET ?messageId=...   -> resolve a quoted message id to the expense it concerns
//                          (bot confirmation refs, with externalId fallback).
// POST { messageId, expenseId } -> record such a link (create-only; the bot uses
//                          `created:false` as an idempotency lock for retries).

export async function GET(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const messageId = new URL(req.url).searchParams.get('messageId')
  if (!messageId) {
    return Response.json(
      { error: 'messageId query param is required' },
      { status: 400 },
    )
  }

  const expenseId = await getExpenseIdByMessage(messageId)
  if (!expenseId) {
    return Response.json(
      { error: 'no expense for that message' },
      { status: 404 },
    )
  }
  const expense = await getBotExpenseDetail(expenseId)
  if (!expense) {
    // The ref outlived its expense (deleted). Treat as not-found.
    return Response.json(
      { error: 'no expense for that message' },
      { status: 404 },
    )
  }
  return Response.json({ expense })
}

export async function POST(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  let body: { messageId?: string; expenseId?: string }
  try {
    body = (await req.json()) as typeof body
  } catch {
    return Response.json({ error: 'invalid JSON body' }, { status: 400 })
  }
  if (!body.messageId || !body.expenseId) {
    return Response.json(
      { error: 'messageId and expenseId are required' },
      { status: 400 },
    )
  }

  const result = await recordMessageRef(body.messageId, body.expenseId)
  return Response.json(result, { status: result.created ? 201 : 200 })
}
