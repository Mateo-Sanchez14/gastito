import { getGroupLinkByChatId, listMembers, upsertMember } from '@/lib/bot'
import { getGroup } from '@/lib/api'
import { requireBotAuth } from '@/lib/bot-auth'

// gastito: sender(JID) -> spliit Participant mapping for a group.
//   GET  ?chatId=...                              -> list mappings
//   POST {chatId, senderJid, participantId, ...}  -> upsert mapping (the /soy command)
export async function GET(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const chatId = new URL(req.url).searchParams.get('chatId')
  if (!chatId) {
    return Response.json({ error: 'chatId query param is required' }, { status: 400 })
  }
  return Response.json({ members: await listMembers(chatId) })
}

export async function POST(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const body = (await req.json().catch(() => null)) as {
    chatId?: string
    senderJid?: string
    participantId?: string
    displayName?: string
  } | null
  if (!body?.chatId || !body?.senderJid || !body?.participantId) {
    return Response.json(
      { error: 'chatId, senderJid and participantId are required' },
      { status: 400 },
    )
  }

  // Validate the participant actually belongs to the linked group.
  const link = await getGroupLinkByChatId(body.chatId)
  if (!link) return Response.json({ error: 'group not linked' }, { status: 404 })
  const group = await getGroup(link.groupId)
  if (!group?.participants.some((p) => p.id === body.participantId)) {
    return Response.json(
      { error: 'participantId is not in the linked group' },
      { status: 400 },
    )
  }

  const member = await upsertMember(
    body.chatId,
    body.senderJid,
    body.participantId,
    body.displayName,
  )
  return Response.json({
    member: {
      senderJid: member.senderJid,
      participantId: member.participantId,
      displayName: member.displayName,
    },
  })
}
