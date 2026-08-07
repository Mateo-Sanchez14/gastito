'use client'

import { useCurrentGroup } from '@/app/groups/[groupId]/current-group-context'
import { CategoryIcon } from '@/app/groups/[groupId]/expenses/category-icon'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Currency } from '@/lib/currency'
import { useActiveUser } from '@/lib/hooks'
import { cn, formatCurrency, getCurrencyFromGroup } from '@/lib/utils'
import { trpc } from '@/trpc/client'
import { useLocale, useTranslations } from 'next-intl'
import { useState } from 'react'

/** Whose money the bars measure: the whole group's, or the active user's share. */
type Scope = 'group' | 'mine'
/** How finely the expenses are bucketed: one bar per category, or per family. */
type Level = 'category' | 'grouping'

type Slice = {
  key: string
  categoryId: number
  name: string
  grouping: string
  amount: number
  expenseCount: number
  participantAmount: number
}

export function CategoryBreakdown() {
  const { groupId, group } = useCurrentGroup()
  const activeUser = useActiveUser(groupId)
  const participantId =
    activeUser && activeUser !== 'None' ? activeUser : undefined

  const { data, isPlaceholderData } = trpc.groups.stats.byCategory.useQuery(
    { groupId, participantId },
    // Hold the previous bars while a refetch lands — no skeleton flash, no
    // layout jump when the active user resolves from local storage.
    { placeholderData: (previous) => previous },
  )

  const [scope, setScope] = useState<Scope>('group')
  const [level, setLevel] = useState<Level>('category')
  const [asTable, setAsTable] = useState(false)

  const t = useTranslations('Stats.ByCategory')
  const locale = useLocale()

  if (!data || !group) return <CategoryBreakdownLoading />

  // Without an active user there is no "mine" to show.
  const effectiveScope: Scope = participantId ? scope : 'group'
  const currency = getCurrencyFromGroup(group)
  const slices = buildSlices(data, level, effectiveScope)
  const total = slices.reduce((sum, slice) => sum + slice.amount, 0)
  const scale = Math.max(...slices.map((slice) => Math.abs(slice.amount)), 1)
  // In "mine" scope the amounts already are the user's share, so repeating it
  // would just restate the bar.
  const showParticipantShare = !!participantId && effectiveScope === 'group'

  return (
    <div className={cn('space-y-4', isPlaceholderData && 'opacity-60')}>
      <div className="flex flex-wrap items-center gap-2">
        {participantId && (
          <Tabs
            value={effectiveScope}
            onValueChange={(value) => setScope(value as Scope)}
          >
            <TabsList className="h-8">
              <TabsTrigger className="text-xs py-1" value="group">
                {t('scopeGroup')}
              </TabsTrigger>
              <TabsTrigger className="text-xs py-1" value="mine">
                {t('scopeMine')}
              </TabsTrigger>
            </TabsList>
          </Tabs>
        )}
        <Tabs value={level} onValueChange={(value) => setLevel(value as Level)}>
          <TabsList className="h-8">
            <TabsTrigger className="text-xs py-1" value="category">
              {t('levelCategory')}
            </TabsTrigger>
            <TabsTrigger className="text-xs py-1" value="grouping">
              {t('levelGrouping')}
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <Tabs
          className="ml-auto"
          value={asTable ? 'table' : 'chart'}
          onValueChange={(value) => setAsTable(value === 'table')}
        >
          <TabsList className="h-8">
            <TabsTrigger className="text-xs py-1" value="chart">
              {t('viewChart')}
            </TabsTrigger>
            <TabsTrigger className="text-xs py-1" value="table">
              {t('viewTable')}
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {slices.length === 0 ? (
        <p className="text-sm text-muted-foreground py-2">
          {effectiveScope === 'mine' ? t('emptyMine') : t('empty')}
        </p>
      ) : asTable ? (
        <SliceTable
          slices={slices}
          total={total}
          currency={currency}
          level={level}
          showParticipantShare={showParticipantShare}
        />
      ) : (
        <div className="space-y-3">
          {slices.map((slice) => (
            <SliceBar
              key={slice.key}
              slice={slice}
              total={total}
              scale={scale}
              currency={currency}
              level={level}
              showParticipantShare={showParticipantShare}
            />
          ))}
          <div className="flex justify-between border-t pt-3 text-sm">
            <span className="text-muted-foreground">{t('total')}</span>
            <span className="font-bold tabular-nums">
              {formatCurrency(currency, total, locale)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

function SliceBar({
  slice,
  total,
  scale,
  currency,
  level,
  showParticipantShare,
}: {
  slice: Slice
  total: number
  scale: number
  currency: Currency
  level: Level
  showParticipantShare: boolean
}) {
  const [open, setOpen] = useState(false)
  const locale = useLocale()
  const t = useTranslations('Stats.ByCategory')
  const label = useSliceLabel(level)

  const share = total === 0 ? 0 : slice.amount / total
  // Bars are scaled against the biggest slice — the job here is comparing
  // categories to each other, not reading each one back as a fraction.
  const width = (Math.abs(slice.amount) / scale) * 100
  const average = slice.expenseCount
    ? slice.amount / slice.expenseCount
    : slice.amount

  return (
    <div>
      {/* A tap target rather than a hover tooltip: this app is read on a phone
          most of the time, and the same gesture works for mouse and keyboard.
          Everything behind it is also in the table view, so nothing is gated. */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        className="w-full text-left rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ring-offset-background"
      >
        <div className="flex items-baseline justify-between gap-3 mb-1.5 text-sm">
          <div className="flex items-center gap-2 min-w-0">
            <CategoryIcon
              category={{
                id: slice.categoryId,
                name: level === 'grouping' ? slice.grouping : slice.name,
                grouping: slice.grouping,
              }}
              className="w-4 h-4 shrink-0 text-muted-foreground"
            />
            <span className="truncate">{label(slice)}</span>
          </div>
          <div className="shrink-0 flex items-baseline gap-2">
            <span className="font-medium tabular-nums">
              {formatCurrency(currency, slice.amount, locale)}
            </span>
            <span className="text-xs text-muted-foreground tabular-nums w-12 text-right">
              {formatShare(share, locale)}
            </span>
          </div>
        </div>
        <div
          aria-hidden
          className="h-2 rounded-r-[4px] overflow-hidden"
          style={{ background: 'var(--chart-1-track)' }}
        >
          <div
            className="h-2 rounded-r-[4px]"
            style={{
              width: `${width}%`,
              minWidth: slice.amount === 0 ? 0 : '3px',
              background: 'var(--chart-1)',
            }}
          />
        </div>
      </button>

      {open && (
        <dl className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-1 text-xs">
          <Detail
            label={t('expenseCount')}
            value={String(slice.expenseCount)}
          />
          <Detail
            label={t('average')}
            value={formatCurrency(currency, average, locale)}
          />
          {showParticipantShare && (
            <Detail
              label={t('yourShare')}
              value={formatCurrency(currency, slice.participantAmount, locale)}
            />
          )}
        </dl>
      )}
    </div>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  )
}

function SliceTable({
  slices,
  total,
  currency,
  level,
  showParticipantShare,
}: {
  slices: Slice[]
  total: number
  currency: Currency
  level: Level
  showParticipantShare: boolean
}) {
  const locale = useLocale()
  const t = useTranslations('Stats.ByCategory')
  const label = useSliceLabel(level)

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>
            {level === 'grouping' ? t('levelGrouping') : t('levelCategory')}
          </TableHead>
          <TableHead className="text-right">{t('amount')}</TableHead>
          <TableHead className="text-right">{t('share')}</TableHead>
          <TableHead className="text-right">{t('count')}</TableHead>
          <TableHead className="text-right">{t('average')}</TableHead>
          {showParticipantShare && (
            <TableHead className="text-right">{t('yourShare')}</TableHead>
          )}
        </TableRow>
      </TableHeader>
      <TableBody>
        {slices.map((slice) => (
          <TableRow key={slice.key}>
            <TableCell>{label(slice)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {formatCurrency(currency, slice.amount, locale)}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {formatShare(total === 0 ? 0 : slice.amount / total, locale)}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {slice.expenseCount}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {formatCurrency(
                currency,
                slice.expenseCount
                  ? slice.amount / slice.expenseCount
                  : slice.amount,
                locale,
              )}
            </TableCell>
            {showParticipantShare && (
              <TableCell className="text-right tabular-nums">
                {formatCurrency(currency, slice.participantAmount, locale)}
              </TableCell>
            )}
          </TableRow>
        ))}
      </TableBody>
      <TableFooter>
        <TableRow>
          <TableCell>{t('total')}</TableCell>
          <TableCell className="text-right tabular-nums">
            {formatCurrency(currency, total, locale)}
          </TableCell>
          <TableCell />
          <TableCell />
        </TableRow>
      </TableFooter>
    </Table>
  )
}

function CategoryBreakdownLoading() {
  return (
    <div className="space-y-3">
      {[0, 1, 2, 3, 4].map((index) => (
        <div key={index}>
          <div className="flex justify-between mb-1.5">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-3 w-16" />
          </div>
          <Skeleton
            className="h-2 rounded-r-[4px]"
            style={{ width: `${100 - index * 16}%` }}
          />
        </div>
      ))}
    </div>
  )
}

/**
 * Turns a slice into its display name. Category rows are translated through the
 * English DB name (spliit uses it as the i18n key); family rows use the
 * grouping's heading. Anything the messages file doesn't know falls back to the
 * raw DB name rather than rendering a key.
 */
function useSliceLabel(level: Level) {
  const t = useTranslations('Categories')
  return (slice: Slice) => {
    const key =
      level === 'grouping'
        ? `${slice.grouping}.heading`
        : `${slice.grouping}.${slice.name}`
    return t.has(key as any)
      ? t(key as any)
      : level === 'grouping'
      ? slice.grouping
      : slice.name
  }
}

function buildSlices(
  data: {
    categories: {
      id: number
      name: string
      grouping: string
      total: number
      participantTotal: number
      expenseCount: number
      participantExpenseCount: number
    }[]
  },
  level: Level,
  scope: Scope,
): Slice[] {
  const buckets = new Map<string, Slice>()

  for (const category of data.categories) {
    const key = level === 'grouping' ? category.grouping : String(category.id)
    const slice = buckets.get(key) ?? {
      key,
      categoryId: category.id,
      name: category.name,
      grouping: category.grouping,
      amount: 0,
      expenseCount: 0,
      participantAmount: 0,
    }

    slice.amount +=
      scope === 'mine' ? category.participantTotal : category.total
    slice.expenseCount +=
      scope === 'mine'
        ? category.participantExpenseCount
        : category.expenseCount
    slice.participantAmount += category.participantTotal

    buckets.set(key, slice)
  }

  return Array.from(buckets.values())
    .filter((slice) => slice.amount !== 0)
    .sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount))
}

function formatShare(share: number, locale: string) {
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 1,
  }).format(share)
}
