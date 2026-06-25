/**
 * gastito: data helpers for the WhatsApp bot integration.
 *
 * These back the `/api/bot/*` route handlers. They wrap Prisma for the
 * WhatsApp-specific tables (group links, member mappings) and a couple of
 * lookups (idempotency, "last expense by sender") that the bot needs but
 * spliit's own UI does not.
 */
import { prisma } from '@/lib/prisma'
import { ActivityType } from '@prisma/client'

export type GroupLink = {
  chatId: string
  groupId: string
  fxArsSource: string
}

/** Resolve a WhatsApp group (chatId "...@g.us") to its linked spliit Group. */
export async function getGroupLinkByChatId(
  chatId: string,
): Promise<GroupLink | null> {
  const link = await prisma.whatsAppGroupLink.findUnique({ where: { chatId } })
  if (!link) return null
  return { chatId: link.chatId, groupId: link.groupId, fxArsSource: link.fxArsSource }
}

/** Create or update the link between a WhatsApp group and a spliit Group. */
export async function upsertGroupLink(
  chatId: string,
  groupId: string,
  fxArsSource?: string,
): Promise<GroupLink> {
  const link = await prisma.whatsAppGroupLink.upsert({
    where: { chatId },
    create: { chatId, groupId, ...(fxArsSource ? { fxArsSource } : {}) },
    update: { groupId, ...(fxArsSource ? { fxArsSource } : {}) },
  })
  return { chatId: link.chatId, groupId: link.groupId, fxArsSource: link.fxArsSource }
}

/** Update just the ARS quote source for a linked group (the /cotizacion command). */
export async function setFxArsSource(chatId: string, fxArsSource: string) {
  return prisma.whatsAppGroupLink.update({
    where: { chatId },
    data: { fxArsSource },
  })
}

export type MemberMapping = {
  senderJid: string
  participantId: string
  displayName: string | null
}

/** List all sender->participant mappings for a WhatsApp group. */
export async function listMembers(chatId: string): Promise<MemberMapping[]> {
  const members = await prisma.whatsAppMember.findMany({ where: { chatId } })
  return members.map((m) => ({
    senderJid: m.senderJid,
    participantId: m.participantId,
    displayName: m.displayName,
  }))
}

/** Map a sender within a group to a spliit Participant (the /soy command). */
export async function upsertMember(
  chatId: string,
  senderJid: string,
  participantId: string,
  displayName?: string,
) {
  return prisma.whatsAppMember.upsert({
    where: { chatId_senderJid: { chatId, senderJid } },
    create: { chatId, senderJid, participantId, displayName },
    update: { participantId, displayName },
  })
}

/** Idempotency lookup: has this Gowa message already produced an expense? */
export async function getExpenseByExternalId(source: string, externalId: string) {
  return prisma.expense.findUnique({
    where: { source_externalId: { source, externalId } },
  })
}

/**
 * Find the most recent bot-created expense logged by a given participant in a
 * group, for the `deshacer`/undo command. Uses the Activity audit log
 * (CREATE_EXPENSE rows carry the creating participantId) and confirms the
 * expense still exists and was created via WhatsApp.
 */
export async function getLastBotExpenseByParticipant(
  groupId: string,
  participantId: string,
): Promise<{ id: string; title: string } | null> {
  const activities = await prisma.activity.findMany({
    where: { groupId, participantId, activityType: ActivityType.CREATE_EXPENSE },
    orderBy: { time: 'desc' },
    take: 20,
  })
  for (const activity of activities) {
    if (!activity.expenseId) continue
    const expense = await prisma.expense.findUnique({
      where: { id: activity.expenseId },
      select: { id: true, title: true, source: true },
    })
    if (expense && expense.source === 'whatsapp') {
      return { id: expense.id, title: expense.title }
    }
  }
  return null
}
