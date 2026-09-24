import {
  Alert,
  Badge,
  Button,
  Group,
  List,
  Modal,
  NumberInput,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { fetchLatestRun, fetchScheduleConfig, startRun, type LatestRun } from '../api/schedule.ts'

const ACTIVE = new Set(['queued', 'running'])
const STATE_LABEL: Record<string, { label: string; color: string }> = {
  not_generated: { label: 'Schedule: not generated', color: 'gray' },
  queued: { label: 'Schedule: starting', color: 'blue' },
  running: { label: 'Schedule: running', color: 'blue' },
  completed: { label: 'Schedule: complete', color: 'green' },
  completed_with_shortages: { label: 'Schedule: complete with shortages', color: 'yellow' },
  failed: { label: 'Schedule: failed', color: 'red' },
}

/** Header control: status of the latest run, progress while running, and "Generate schedule". */
export function ScheduleRunControl() {
  const client = useQueryClient()
  const latest = useQuery({
    queryKey: ['schedule-latest'],
    queryFn: fetchLatestRun,
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.state) ? 1500 : false),
  })
  const [open, setOpen] = useState(false)

  // When a run finishes, refresh everything that shows schedule results.
  const previous = useRef<string | undefined>(undefined)
  const state = latest.data?.state
  useEffect(() => {
    if (previous.current && ACTIVE.has(previous.current) && state && !ACTIVE.has(state)) {
      for (const key of ['queue-schedule', 'agent-schedule', 'schedule-summary']) {
        void client.invalidateQueries({ queryKey: [key] })
      }
    }
    previous.current = state
  }, [state, client])

  const data = latest.data
  const info = STATE_LABEL[data?.state ?? 'not_generated'] ?? STATE_LABEL.not_generated
  const running = data && ACTIVE.has(data.state)

  return (
    <Group gap="xs">
      {running ? (
        <Stack gap={2} w={260}>
          <Text size="xs" c="dimmed" truncate>
            {data.message} ({data.done ?? 0}/{data.total ?? 0})
          </Text>
          <Progress value={data.total ? (100 * (data.done ?? 0)) / data.total : 5} size="sm" animated aria-label="Schedule run progress" />
        </Stack>
      ) : (
        <RunBadge data={data} label={info.label} color={info.color} />
      )}
      <Button size="xs" onClick={() => setOpen(true)} disabled={!!running}>
        Generate schedule
      </Button>
      <GenerateModal opened={open} onClose={() => setOpen(false)} />
    </Group>
  )
}

function RunBadge({ data, label, color }: { data: LatestRun | undefined; label: string; color: string }) {
  const s = data?.summary
  const details = [
    data?.state === 'failed' && data.message ? `Error: ${data.message}` : null,
    s ? `Last completed run ${s.run_id}` : null,
    s ? `Queues: ${Object.entries(s.status_counts).map(([k, v]) => `${v} ${k}`).join(', ')}` : null,
    s ? `Shortage: ${Math.round(s.shortage_agent_hours).toLocaleString()} agent-hours` : null,
    s ? `Hard-rule validation: ${s.validation_passed ? 'passed' : 'FAILED'}` : null,
    s ? `Runtime: ${Math.round(s.runtime_seconds)} s` : null,
    s && s.conflicts.length ? `Configuration conflicts: ${s.conflicts.length}` : null,
    data?.stale ? 'Inputs changed since the last run — regenerate' : null,
  ].filter(Boolean)
  return (
    <Tooltip multiline w={360} label={details.join('\n') || 'No schedule run yet'} style={{ whiteSpace: 'pre-line' }}>
      <Badge color={data?.stale ? 'orange' : color} variant="light">
        {data?.stale ? `${label} (stale)` : label}
      </Badge>
    </Tooltip>
  )
}

function GenerateModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const client = useQueryClient()
  const config = useQuery({ queryKey: ['schedule-config'], queryFn: fetchScheduleConfig, enabled: opened })
  const [values, setValues] = useState<Record<string, number>>({})
  const start = useMutation({
    mutationFn: startRun,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['schedule-latest'] })
      onClose()
    },
  })

  const s = config.data?.staffing
  const sch = config.data?.scheduling
  const get = (key: string, fallback: number) => values[key] ?? fallback
  const set = (key: string) => (v: string | number) => setValues((prev) => ({ ...prev, [key]: Number(v) }))

  function submit() {
    if (!s || !sch) return
    start.mutate({
      staffing: {
        service_level_target: get('sl', s.service_level_target * 100) / 100,
        answer_threshold_seconds: get('threshold', s.answer_threshold_seconds),
        max_occupancy: get('occupancy', s.max_occupancy * 100) / 100,
        residual_shrinkage: get('shrinkage', s.residual_shrinkage * 100) / 100,
        closing_minutes: get('closing', s.closing_minutes),
      },
      scheduling: {
        break_stagger_slots: get('stagger', sch.break_stagger_slots),
        stage_time_limit_seconds: [
          get('t1', sch.stage_time_limit_seconds[0]),
          get('t2', sch.stage_time_limit_seconds[1]),
          get('t3', sch.stage_time_limit_seconds[2]),
        ],
      },
    })
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Generate schedule" size="lg">
      {!s || !sch ? (
        <Text size="sm">Loading settings…</Text>
      ) : (
        <Stack gap="sm">
          <Text size="sm">
            Uses the saved queue forecasts: 15-minute requirements from Erlang C, then CP-SAT assigns every agent in
            their home queue. The run takes several minutes for all queues.
          </Text>
          <Text size="sm" fw={600}>
            Staffing targets
          </Text>
          <SimpleGrid cols={3}>
            <NumberInput label="Service level %" min={1} max={99} value={get('sl', s.service_level_target * 100)} onChange={set('sl')} />
            <NumberInput label="Answered within (s)" min={1} value={get('threshold', s.answer_threshold_seconds)} onChange={set('threshold')} />
            <NumberInput label="Max occupancy %" min={1} max={100} value={get('occupancy', s.max_occupancy * 100)} onChange={set('occupancy')} />
            <NumberInput
              label="Residual shrinkage %"
              description="Unplanned only"
              min={0}
              max={90}
              value={get('shrinkage', s.residual_shrinkage * 100)}
              onChange={set('shrinkage')}
            />
            <NumberInput
              label="Closing work (min)"
              description="Coverage after close"
              min={0}
              max={120}
              step={15}
              value={get('closing', s.closing_minutes)}
              onChange={set('closing')}
            />
          </SimpleGrid>
          <Text size="sm" fw={600}>
            Solver
          </Text>
          <SimpleGrid cols={4}>
            <NumberInput
              label="Break stagger (slots)"
              description="0 = sheet times"
              min={0}
              max={4}
              value={get('stagger', sch.break_stagger_slots)}
              onChange={set('stagger')}
            />
            <NumberInput label="Stage 1 limit (s)" description="Shortage" min={1} value={get('t1', sch.stage_time_limit_seconds[0])} onChange={set('t1')} />
            <NumberInput label="Stage 2 limit (s)" description="Excess + paid" min={1} value={get('t2', sch.stage_time_limit_seconds[1])} onChange={set('t2')} />
            <NumberInput label="Stage 3 limit (s)" description="Preferences" min={1} value={get('t3', sch.stage_time_limit_seconds[2])} onChange={set('t3')} />
          </SimpleGrid>
          <Alert color="gray" title="What this does and doesn't prove">
            <List size="xs">
              <List.Item>Erlang C gives approximate interval requirements; it ignores abandonment.</List.Item>
              <List.Item>A feasible schedule doesn't by itself prove real service levels will be met.</List.Item>
              <List.Item>Residual shrinkage is a planning allowance, never assigned to an agent.</List.Item>
            </List>
          </Alert>
          {start.isError && (
            <Alert color="red" title="Could not start">
              {start.error.message}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={submit} loading={start.isPending}>
              Start run
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  )
}
