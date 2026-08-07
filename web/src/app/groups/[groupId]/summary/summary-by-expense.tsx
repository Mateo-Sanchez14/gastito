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

/** The mirror view: one row per expense, opened to show who owes what on it. */
export function SummaryByExpense({
  summary,
  currency,
  groupId,
  activeUserId,
}: Props) {
  const t = useTranslations('Summary')

  if (summary.expenses.length === 0)
    return <p className="text-sm text-muted-foreground">{t('empty')}</p>

  return (
    <div className="divide-y">
      {summary.expenses.map((expense) => (
        <ExpenseRow
          key={expense.id}
          expense={expense}
          currency={currency}
          groupId={groupId}
          activeUserId={activeUserId}
        />
      ))}
    </div>
  )
}

function ExpenseRow({
  expense,
  currency,
  groupId,
  activeUserId,
}: {
  expense: Summary['expenses'][number]
  currency: Currency
  groupId: string
  activeUserId?: string
}) {
  const [open, setOpen] = useState(false)
  const locale = useLocale()
  const t = useTranslations('Summary')

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="w-full text-left py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm">
        <div className="flex justify-between gap-3">
          <div className="flex items-center gap-1.5 min-w-0">
            <ChevronRight
              className={cn(
                'w-4 h-4 shrink-0 text-muted-foreground transition-transform',
                open && 'rotate-90',
              )}
            />
            <CategoryIcon
              category={expense.category}
              className="w-4 h-4 shrink-0 text-muted-foreground"
            />
            <div className="min-w-0">
              <div className="truncate">{expense.title}</div>
              <div className="text-xs text-muted-foreground truncate">
                {formatDateOnly(expense.expenseDate, locale, {
                  dateStyle: 'medium',
                })}
                {' · '}
                {t('paidBy', { name: expense.paidBy.name })}
              </div>
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div className="font-bold tabular-nums">
              {formatCurrency(currency, expense.amount, locale)}
            </div>
            <div className="text-xs text-muted-foreground">
              {t('splitBetween', { count: expense.paidFor.length })}
            </div>
          </div>
        </div>
      </CollapsibleTrigger>

      <CollapsibleContent>
        <ul className="pl-10 pb-3 text-sm">
          {expense.paidFor.map((participant) => (
            <li
              key={participant.id}
              className="flex justify-between gap-3 py-1"
            >
              <span
                className={cn(
                  'truncate',
                  participant.id === activeUserId && 'font-medium',
                )}
              >
                {participant.name}
                {participant.id === activeUserId && (
                  <span className="text-xs text-muted-foreground">
                    {' '}
                    ({t('you')})
                  </span>
                )}
              </span>
              <span className="shrink-0 tabular-nums">
                {formatCurrency(currency, participant.amount, locale)}
              </span>
            </li>
          ))}
          <li className="pt-2">
            <Link
              href={`/groups/${groupId}/expenses/${expense.id}/edit`}
              className="text-xs text-muted-foreground underline underline-offset-2"
            >
              {t('openExpense')}
            </Link>
          </li>
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}
