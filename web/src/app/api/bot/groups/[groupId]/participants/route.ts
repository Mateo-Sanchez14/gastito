import { getCategories, getGroup } from '@/lib/api'
import { listAliasesByParticipant, setParticipantsActive } from '@/lib/bot'
import { requireBotAuth } from '@/lib/bot-auth'

// gastito: list a group's participants (+ categories + currency) so the bot can
// resolve names from a natural-language message to participant ids.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ groupId: string }> },
) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const { groupId } = await ctx.params
  const group = await getGroup(groupId)
  if (!group) {
    return Response.json({ error: 'unknown groupId' }, { status: 404 })
  }
  const categories = await getCategories()
  const aliasesById = await listAliasesByParticipant(groupId)

  return Response.json({
    group: {
      id: group.id,
      name: group.name,
      currency: group.currency,
      currencyCode: group.currencyCode,
    },
    participants: group.participants.map((p) => ({
      id: p.id,
      name: p.name,
      // "presente": whether a default "entre todos" split covers them.
      active: p.active,
      aliases: aliasesById[p.id] ?? [],
    })),
    categories: categories.map((c) => ({
      id: c.id,
      name: c.name,
      grouping: c.grouping,
    })),
  })
}

// gastito: mark participants present/absent (the /entra and /sale commands).
// Nobody is deleted — this only moves people in and out of the default
// "entre todos" split. Idempotent; ids already in that state come back under
// `unchanged` so the bot can answer "ya estaba".
//   PATCH {participantIds: string[], active: boolean}
export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ groupId: string }> },
) {
  const unauthorized = requireBotAuth(req)
  if (unauthorized) return unauthorized

  const { groupId } = await ctx.params
  const body = (await req.json().catch(() => null)) as {
    participantIds?: string[]
    active?: boolean
  } | null
  // `active` is checked by type, not truthiness: `false` is the whole point of /sale.
  if (!body?.participantIds?.length || typeof body.active !== 'boolean') {
    return Response.json(
      {
        error: 'a non-empty participantIds[] and a boolean active are required',
      },
      { status: 400 },
    )
  }

  const group = await getGroup(groupId)
  if (!group)
    return Response.json({ error: 'unknown groupId' }, { status: 404 })
  const groupParticipantIds = new Set(group.participants.map((p) => p.id))
  const foreign = body.participantIds.filter(
    (id) => !groupParticipantIds.has(id),
  )
  if (foreign.length > 0) {
    return Response.json(
      {
        error: `participantIds do not belong to this group: ${foreign.join(
          ', ',
        )}`,
      },
      { status: 404 },
    )
  }

  const result = await setParticipantsActive(
    groupId,
    body.participantIds,
    body.active,
  )
  return Response.json(result)
}
