import { Alert, Loader, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import type { Organization } from '../api/hierarchy.ts'
import { fetchScheduleSummary } from '../api/schedule.ts'
import { ScheduleTotalsTable } from './QueueSchedulePanel.tsx'

type Props = { level: 'org' | 'bu' | 'mu'; code: string; org: Organization }

/** Schedule totals for each child unit plus their sum, which equals this unit's total. */
export function ScheduleSummary({ level, code, org }: Props) {
  const query = useQuery({
    queryKey: ['schedule-summary', level, code],
    queryFn: () => fetchScheduleSummary(level, code),
    retry: false,
  })
  if (query.isPending) return <Loader size="sm" />
  if (query.isError)
    return (
      <Alert color="gray" title="No schedule yet">
        {query.error.message}
      </Alert>
    )

  const names = new Map<string, string>()
  for (const bu of org.business_units) {
    names.set(bu.code, bu.name)
    for (const mu of bu.management_units) {
      names.set(mu.code, mu.name)
      for (const q of mu.queues) names.set(q.id, q.name)
    }
  }
  const rows = [
    ...query.data.children.map((c) => ({ label: `${names.get(c.code) ?? c.code} (${c.code})`, totals: c })),
    { label: 'Total', totals: query.data.total },
  ]
  return (
    <Stack gap="xs">
      <Text size="sm" c="dimmed">
        Schedule run {query.data.run_id}: totals by {level === 'org' ? 'business unit' : level === 'bu' ? 'management unit' : 'queue'}.
        Hours are summed across intervals; concurrent agents are only ever reported as peaks.
      </Text>
      <ScheduleTotalsTable rows={rows} />
    </Stack>
  )
}
