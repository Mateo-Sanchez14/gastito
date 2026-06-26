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
 * Record that a WhatsApp message id refers to an expense (the bot's confirmation
 * messages, so replying to one later resolves the expense to edit). Create-only:
 * returns `created: false` if the id was already recorded, which the bot also
 * uses as an idempotency lock for an inbound edit message.
 */
export async function recordMessageRef(messageId: string, expenseId: string) {
  const existing = await prisma.whatsAppMessageRef.findUnique({
    where: { messageId },
  })
  if (existing) return { created: false, expenseId: existing.expenseId }
  try {
    await prisma.whatsAppMessageRef.create({ data: { messageId, expenseId } })
    return { created: true, expenseId }
  } catch (err) {
    // A concurrent retry won the race; treat as already-recorded.
    const message = err instanceof Error ? err.message : String(err)
    if (message.includes('Unique constraint')) {
      return { created: false, expenseId }
    }
    throw err
  }
}

/**
 * Resolve a quoted WhatsApp message id to the expense it concerns. Checks the
 * message-ref table first (bot confirmations), then falls back to the expense's
 * own `externalId` (a member replying to their own original message).
 */
export async function getExpenseIdByMessage(
  messageId: string,
): Promise<string | null> {
  const ref = await prisma.whatsAppMessageRef.findUnique({
    where: { messageId },
    select: { expenseId: true },
  })
  if (ref) return ref.expenseId
  const expense = await prisma.expense.findUnique({
    where: { source_externalId: { source: 'whatsapp', externalId: messageId } },
    select: { id: true },
  })
  return expense?.id ?? null
}

export type BotExpenseDetail = {
  id: string
  groupId: string
  title: string
  categoryId: number
  amount: number // group-currency (USD) cents
  originalAmount: number | null
  originalCurrency: string | null
  conversionRate: number | null
  paidById: string
  paidForIds: string[]
  splitMode: string
  expenseDate: string // ISO date (YYYY-MM-DD)
}

/** Bot-friendly snapshot of an expense, used as context when applying an edit. */
export async function getBotExpenseDetail(
  expenseId: string,
): Promise<BotExpenseDetail | null> {
  const e = await prisma.expense.findUnique({
    where: { id: expenseId },
    include: { paidFor: true },
  })
  if (!e) return null
  return {
    id: e.id,
    groupId: e.groupId,
    title: e.title,
    categoryId: e.categoryId,
    amount: e.amount,
    originalAmount: e.originalAmount,
    originalCurrency: e.originalCurrency,
    conversionRate: e.conversionRate ? Number(e.conversionRate) : null,
    paidById: e.paidById,
    paidForIds: e.paidFor.map((pf) => pf.participantId),
    splitMode: e.splitMode,
    expenseDate: e.expenseDate.toISOString().slice(0, 10),
  }
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
