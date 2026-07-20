import { getCategories, getGroup } from '@/lib/api'
import { listAliasesByParticipant } from '@/lib/bot'
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
      aliases: aliasesById[p.id] ?? [],
    })),
    categories: categories.map((c) => ({
      id: c.id,
      name: c.name,
      grouping: c.grouping,
    })),
  })
}
