'use client'

import { useCurrentGroup } from '@/app/groups/[groupId]/current-group-context'
import { SummaryByExpense } from '@/app/groups/[groupId]/summary/summary-by-expense'
import { SummaryByParticipant } from '@/app/groups/[groupId]/summary/summary-by-participant'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useActiveUser } from '@/lib/hooks'
import { formatCurrency, getCurrencyFromGroup } from '@/lib/utils'
import { trpc } from '@/trpc/client'
import { useLocale, useTranslations } from 'next-intl'
import { useState } from 'react'

type View = 'participant' | 'expense'

export function SummaryPageClient() {
  const { groupId, group } = useCurrentGroup()
  const activeUser = useActiveUser(groupId)
  const activeUserId =
    activeUser && activeUser !== 'None' ? activeUser : undefined

  const { data } = trpc.groups.summary.get.useQuery({ groupId })
  const [view, setView] = useState<View>('participant')

  const t = useTranslations('Summary')
  const locale = useLocale()

  return (
    <Card className="mb-4">
      <CardHeader>
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!data || !group ? (
          <SummaryLoading />
        ) : (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
              <div>
                <div className="text-sm text-muted-foreground">
                  {t('total')}
                </div>
                <div className="text-2xl font-semibold">
                  {formatCurrency(
                    getCurrencyFromGroup(group),
                    data.total,
                    locale,
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t('across', {
                    expenses: data.expenseCount,
                    people: data.participants.length,
                  })}
                </div>
              </div>
              <Tabs
                value={view}
                onValueChange={(value) => setView(value as View)}
              >
                <TabsList className="h-8">
                  <TabsTrigger className="text-xs py-1" value="participant">
                    {t('byParticipant')}
                  </TabsTrigger>
                  <TabsTrigger className="text-xs py-1" value="expense">
                    {t('byExpense')}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </div>

            {view === 'participant' ? (
              <SummaryByParticipant
                summary={data}
                currency={getCurrencyFromGroup(group)}
                groupId={groupId}
                activeUserId={activeUserId}
              />
            ) : (
              <SummaryByExpense
                summary={data}
                currency={getCurrencyFromGroup(group)}
                groupId={groupId}
                activeUserId={activeUserId}
              />
            )}

            <p className="text-xs text-muted-foreground">
              {t('reimbursementNote')}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function SummaryLoading() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-40" />
      {[0, 1, 2, 3].map((index) => (
        <div key={index} className="space-y-1.5">
          <div className="flex justify-between">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-3 w-16" />
          </div>
          <Skeleton
            className="h-2 rounded-r-[4px]"
            style={{ width: `${100 - index * 18}%` }}
          />
        </div>
      ))}
    </div>
  )
}
