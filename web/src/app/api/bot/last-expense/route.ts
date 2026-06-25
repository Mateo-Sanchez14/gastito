import { getLastBotExpenseByParticipant } from '@/lib/bot'
import { requireBotAuth } from '@/lib/bot-auth'

// gastito: most recent bot-created expense logged by a participant, for the
// `deshacer`/undo command. Returns 404 if the sender has nothing to undo.
export async function GET(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const url = new URL(req.url)
  const groupId = url.searchParams.get('groupId')
  const participantId = url.searchParams.get('participantId')
  if (!groupId || !participantId) {
    return Response.json(
      { error: 'groupId and participantId query params are required' },
      { status: 400 },
    )
  }

  const expense = await getLastBotExpenseByParticipant(groupId, participantId)
  if (!expense) return Response.json({ error: 'nothing to undo' }, { status: 404 })
  return Response.json({ expense })
}
