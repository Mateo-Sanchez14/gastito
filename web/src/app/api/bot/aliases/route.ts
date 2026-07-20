import { getGroup } from '@/lib/api'
import { addAliases } from '@/lib/bot'
import { requireBotAuth } from '@/lib/bot-auth'

// gastito: attach nicknames (apodos) to a participant. The bot resolves the
// target participant to an id first (against names + existing aliases), then
// POSTs the new apodo(s) here. Idempotent per (group, apodo); an apodo already
// used by someone else comes back under `conflicts` (never reassigned).
//   POST {groupId, participantId, aliases: string[]}
export async function POST(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const body = (await req.json().catch(() => null)) as {
    groupId?: string
    participantId?: string
    aliases?: string[]
  } | null
  if (!body?.groupId || !body?.participantId || !body?.aliases?.length) {
    return Response.json(
      { error: 'groupId, participantId and a non-empty aliases[] are required' },
      { status: 400 },
    )
  }

  const group = await getGroup(body.groupId)
  if (!group) return Response.json({ error: 'unknown groupId' }, { status: 404 })
  if (!group.participants.some((p) => p.id === body.participantId)) {
    return Response.json(
      { error: 'participantId does not belong to this group' },
      { status: 404 },
    )
  }

  const result = await addAliases(body.groupId, body.participantId, body.aliases)
  return Response.json(result, { status: 201 })
}
