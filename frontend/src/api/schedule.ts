import type { AgentRecord } from './agents.ts'
import { errorDetail } from './hierarchy.ts'

export type ActivityType = 'On Phone' | 'Paid Break' | 'Unpaid Meal' | 'Leave' | 'Training'
export type Activity = {
  activity_type: ActivityType
  start: string // ISO with offset, e.g. 2026-09-23T10:00:00-04:00
  end: string
  paid: boolean
  minutes?: number
  queue_id?: string | null
}
export type DayStatus = 'Working' | 'Off' | 'Leave' | 'Unscheduled' | 'Outside horizon'

// --- Agent calendar (real plan, or the labelled mock in the same shape) --------------------

export type PlanDay = {
  date: string
  weekday: string
  status: DayStatus
  off: boolean
  shift_code?: string | null
  shift_name?: string | null
  start?: string | null
  end?: string | null
  paid_hours?: number
  preferred_shift_matched?: boolean | null
  preferred_day_off?: boolean | null
  note?: string | null
  activities?: Activity[]
}

export type PlanWeek = {
  week_start: string
  paid_hours: number
  days_off: number
  leave_days: number
  preferred_shift_matches: number | null
  working_days: number
  partial: boolean
  days: PlanDay[]
}

export type AgentPlan = {
  source: 'optimized' | 'mock'
  run_id: string | null
  validation_status: string
  queue_status: string | null
  timezone: string
  weeks: PlanWeek[]
}

export type AgentScheduleResponse = { agent: AgentRecord; schedule: AgentPlan }

export async function fetchAgentSchedule(agentId: string): Promise<AgentScheduleResponse> {
  const response = await fetch(`/api/agents/${encodeURIComponent(agentId)}/schedule`)
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load schedule'))
  return (await response.json()) as AgentScheduleResponse
}

// --- Runs ---------------------------------------------------------------------------------

export type RunState = 'not_generated' | 'queued' | 'running' | 'completed' | 'completed_with_shortages' | 'failed'

export type StageResult = { stage: string; status: string; objective: number | null; bound: number | null; seconds: number }

export type RunSummary = {
  run_id: string
  created_at: string
  horizon: { start: string; end: string; days: number }
  status_counts: Record<string, number>
  shortage_agent_hours: number
  validation_passed: boolean
  runtime_seconds: number
  conflicts: string[]
  excluded_agents: { agent_id: string; queue_id: string; reason: string }[]
  config: { staffing: StaffingConfig; scheduling: SchedulingConfig }
  assumptions: string[]
}

export type LatestRun = {
  state: RunState
  run_id?: string
  message?: string
  done?: number
  total?: number
  latest_completed_run_id?: string | null
  summary?: RunSummary
  stale?: boolean
}

export type StaffingConfig = {
  interval_minutes: number
  service_level_target: number
  answer_threshold_seconds: number
  max_occupancy: number
  residual_shrinkage: number
  closing_minutes: number
  within_hour_proportions: number[] | null
}

export type SchedulingConfig = {
  break_stagger_slots: number
  stage_time_limit_seconds: number[]
  num_workers: number
  leave_csv: string | null
  assume_rested_at_start: boolean
  weights: Record<string, number>
}

export async function fetchLatestRun(): Promise<LatestRun> {
  const response = await fetch('/api/schedule/runs/latest')
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load schedule status'))
  return (await response.json()) as LatestRun
}

export async function fetchScheduleConfig(): Promise<{ staffing: StaffingConfig; scheduling: SchedulingConfig }> {
  const response = await fetch('/api/schedule/config')
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load schedule settings'))
  return (await response.json()) as { staffing: StaffingConfig; scheduling: SchedulingConfig }
}

export async function startRun(overrides: {
  staffing: Partial<StaffingConfig>
  scheduling: Partial<SchedulingConfig>
}): Promise<{ run_id: string }> {
  const response = await fetch('/api/schedule/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(overrides),
  })
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to start schedule run'))
  return (await response.json()) as { run_id: string }
}

// --- Queue schedule -------------------------------------------------------------------------

export type CoverageRow = {
  queue_id: string
  interval_start: string
  interval_end: string
  interval_minutes: number
  kind: 'open' | 'closing' | 'no demand'
  required_on_phone: number
  scheduled_on_phone: number
  shortage: number
  excess: number
}

export type ScheduleDay = {
  work_date: string
  status: DayStatus
  shift_code: string | null
  shift_name: string | null
  shift_start: string | null
  shift_end: string | null
  paid_minutes: number
  unpaid_minutes: number
  preferred_shift_matched: boolean | null
  preferred_day_off: boolean | null
  note: string | null
  activities: Activity[]
}

export type ScheduleAgent = {
  agent_id: string
  agent_name: string
  paid_minutes: number
  working_days: number
  preferred_shift_matches: number
  days: ScheduleDay[]
}

export type Totals = {
  required_on_phone_hours: number
  scheduled_on_phone_hours: number
  shortage_agent_hours: number
  excess_agent_hours: number
  paid_hours: number
  leave_hours: number
  working_shifts: number
  agents: number
  peak_required_on_phone: number
}

export type QueueSchedule = {
  run_id: string
  created_at: string
  horizon: { start: string; end: string; days: number }
  stale: boolean
  validation_passed: boolean
  queue: { queue_id: string; status: string; stages: StageResult[]; shortage_agent_hours?: number }
  totals: Totals
  coverage: CoverageRow[]
  agents: ScheduleAgent[]
}

export async function fetchQueueSchedule(queueId: string): Promise<QueueSchedule> {
  const response = await fetch(`/api/queues/${encodeURIComponent(queueId)}/schedule`)
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load schedule'))
  return (await response.json()) as QueueSchedule
}

export type ScheduleSummary = {
  run_id: string
  level: 'org' | 'bu' | 'mu'
  code: string
  children: (Totals & { code: string })[]
  total: Totals
}

export async function fetchScheduleSummary(level: 'org' | 'bu' | 'mu', code: string): Promise<ScheduleSummary> {
  const params = new URLSearchParams({ level, code })
  const response = await fetch(`/api/schedule/summary?${params}`)
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load schedule summary'))
  return (await response.json()) as ScheduleSummary
}

export function exportUrl(runId: string, kind: 'shifts' | 'activities' | 'coverage' | 'requirements', queueId?: string): string {
  const q = queueId ? `?queue_id=${encodeURIComponent(queueId)}` : ''
  return `/api/schedule/runs/${encodeURIComponent(runId)}/export/${kind}.csv${q}`
}
