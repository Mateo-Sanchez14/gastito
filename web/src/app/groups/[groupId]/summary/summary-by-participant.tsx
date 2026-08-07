'use client'

import { CategoryIcon } from '@/app/groups/[groupId]/expenses/category-icon'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Currency } from '@/lib/currency'
import { cn, formatCurrency, formatDateOnly } from '@/lib/utils'
import { AppRouterOutput } from '@/trpc/routers/_app'
import { ChevronRight } from 'lucide-react'
import { useLocale, useTranslations } from 'next-intl'
import Link from 'next/link'
import { useState } from 'react'

type Summary = AppRouterOutput['groups']['summary']['get']

type Props = {
  summary: Summary
  currency: Currency
  groupId: string
  activeUserId?: string
}

/**
 * One row per participant: what they owe in total, and — once opened — every
 * expense they were included in with their own slice of it.
 */
export function SummaryByParticipant({
  summary,
  currency,
  groupId,
  activeUserId,
}: Props) {
  const t = useTranslations('Summary')
  const participants = [...summary.participants].sort(
    (a, b) => b.share - a.share,
  )
  const scale = Math.max(...participants.map((p) => Math.abs(p.share)), 1)

  if (participants.length === 0)
    return <p className="text-sm text-muted-foreground">{t('empty')}</p>

  return (
    <div className="divide-y">
      {participants.map((participant) => (
        <ParticipantRow
          key={participant.id}
          participant={participant}
          expenses={summary.expenses.filter((expense) =>
            expense.paidFor.some((p) => p.id === participant.id),
          )}
          scale={scale}
          currency={currency}
          groupId={groupId}
          isActiveUser={participant.id === activeUserId}
          defaultOpen={participant.id === activeUserId}
        />
      ))}
    </div>
  )
}

function ParticipantRow({
  participant,
  expenses,
  scale,
  currency,
  groupId,
  isActiveUser,
  defaultOpen,
}: {
  participant: Summary['participants'][number]
  expenses: Summary['expenses']
  scale: number
  currency: Currency
  groupId: string
  isActiveUser: boolean
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const locale = useLocale()
  const t = useTranslations('Summary')

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="w-full text-left py-3 group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm">
        <div className="flex items-baseline justify-between gap-3 mb-1.5">
          <div className="flex items-center gap-1.5 min-w-0">
            <ChevronRight
              className={cn(
                'w-4 h-4 shrink-0 text-muted-foreground transition-transform',
                open && 'rotate-90',
              )}
            />
            <span className="truncate font-medium">{participant.name}</span>
            {isActiveUser && (
              <span className="text-xs text-muted-foreground shrink-0">
                ({t('you')})
              </span>
            )}
          </div>
          <div className="shrink-0 text-right">
            <div className="font-bold tabular-nums">
              {formatCurrency(currency, participant.share, locale)}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 pl-5">
          <div
            aria-hidden
            className="h-2 flex-1 rounded-r-[4px] overflow-hidden"
            style={{ background: 'var(--chart-1-track)' }}
          >
            <div
              className="h-2 rounded-r-[4px]"
              style={{
                width: `${(Math.abs(participant.share) / scale) * 100}%`,
                minWidth: participant.share === 0 ? 0 : '3px',
                background: 'var(--chart-1)',
              }}
            />
          </div>
          <span className="text-xs text-muted-foreground tabular-nums shrink-0">
            {t('expenseCount', { count: participant.expenseCount })}
          </span>
        </div>
      </CollapsibleTrigger>

      <CollapsibleContent>
        <ul className="pl-5 pb-3 pt-1 text-sm">
          {expenses.map((expense) => {
            const share = expense.paidFor.find((p) => p.id === participant.id)
              ?.amount

            return (
              <li key={expense.id}>
                <Link
                  href={`/groups/${groupId}/expenses/${expense.id}/edit`}
                  className="flex justify-between gap-3 py-1.5 rounded-sm hover:bg-accent -mx-2 px-2"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <CategoryIcon
                      category={expense.category}
                      className="w-3.5 h-3.5 shrink-0 text-muted-foreground"
                    />
                    <span className="truncate">{expense.title}</span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="tabular-nums">
                      {formatCurrency(currency, share ?? 0, locale)}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {formatDateOnly(expense.expenseDate, locale, {
                        dateStyle: 'short',
                      })}
                      {' · '}
                      {t('paidBy', { name: expense.paidBy.name })}
                    </span>
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}
