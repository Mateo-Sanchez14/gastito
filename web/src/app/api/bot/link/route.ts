import { getGroup } from '@/lib/api'
import {
  getGroupLinkByChatId,
  setFxArsSource,
  upsertGroupLink,
} from '@/lib/bot'
import { requireBotAuth } from '@/lib/bot-auth'

const FX_SOURCES = ['oficial', 'blue', 'mep'] as const

// gastito: WhatsApp-group <-> spliit-Group link management.
//   GET   ?chatId=...        -> resolve a group link (the bot's first lookup)
//   POST  {chatId, groupId}  -> create/update the link (admin / future /vincular)
//   PATCH {chatId, fxArsSource} -> change the ARS quote source (/cotizacion)
export async function GET(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const chatId = new URL(req.url).searchParams.get('chatId')
  if (!chatId) {
    return Response.json({ error: 'chatId query param is required' }, { status: 400 })
  }
  const link = await getGroupLinkByChatId(chatId)
  if (!link) return Response.json({ error: 'not linked' }, { status: 404 })
  return Response.json({ link })
}

export async function POST(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const body = (await req.json().catch(() => null)) as {
    chatId?: string
    groupId?: string
    fxArsSource?: string
  } | null
  if (!body?.chatId || !body?.groupId) {
    return Response.json({ error: 'chatId and groupId are required' }, { status: 400 })
  }
  if (body.fxArsSource && !FX_SOURCES.includes(body.fxArsSource)) {
    return Response.json(
      { error: `fxArsSource must be one of ${FX_SOURCES.join(', ')}` },
      { status: 400 },
    )
  }
  const group = await getGroup(body.groupId)
  if (!group) return Response.json({ error: 'unknown groupId' }, { status: 404 })

  const link = await upsertGroupLink(body.chatId, body.groupId, body.fxArsSource)
  return Response.json({ link })
}

export async function PATCH(req: Request) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const body = (await req.json().catch(() => null)) as {
    chatId?: string
    fxArsSource?: string
  } | null
  if (!body?.chatId || !body?.fxArsSource) {
    return Response.json(
      { error: 'chatId and fxArsSource are required' },
      { status: 400 },
    )
  }
  if (!FX_SOURCES.includes(body.fxArsSource)) {
    return Response.json(
      { error: `fxArsSource must be one of ${FX_SOURCES.join(', ')}` },
      { status: 400 },
    )
  }
  const existing = await getGroupLinkByChatId(body.chatId)
  if (!existing) return Response.json({ error: 'not linked' }, { status: 404 })

  await setFxArsSource(body.chatId, body.fxArsSource)
  return Response.json({ link: { ...existing, fxArsSource: body.fxArsSource } })
}
