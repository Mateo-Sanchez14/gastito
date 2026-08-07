import { createTRPCRouter } from '@/trpc/init'
import { getGroupSummaryProcedure } from '@/trpc/routers/groups/summary/get.procedure'

export const groupSummaryRouter = createTRPCRouter({
  get: getGroupSummaryProcedure,
})
