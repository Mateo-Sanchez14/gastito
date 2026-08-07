import { SummaryPageClient } from '@/app/groups/[groupId]/summary/page.client'
import { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Summary',
}

export default async function SummaryPage() {
  return <SummaryPageClient />
}
