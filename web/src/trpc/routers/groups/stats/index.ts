import { createTRPCRouter } from '@/trpc/init'
import { getGroupStatsByCategoryProcedure } from '@/trpc/routers/groups/stats/byCategory.procedure'
import { getGroupStatsProcedure } from '@/trpc/routers/groups/stats/get.procedure'

export const groupStatsRouter = createTRPCRouter({
  get: getGroupStatsProcedure,
  byCategory: getGroupStatsByCategoryProcedure,
})
