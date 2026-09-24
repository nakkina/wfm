import type { AgentRecord } from './agents.ts'
import { errorDetail } from './hierarchy.ts'

export type SegmentKind = 'work' | 'break' | 'meal'
export type Segment = { kind: SegmentKind; start: string; end: string } // "HH:MM:SS"

export type ScheduleDay = {
  date: string
  weekday: string
  off: boolean
  shift_code: string | null
  shift_name: string | null
  start: string | null
  end: string | null
  paid_hours: number
  segments: Segment[]
}

export type ScheduleWeek = { week_start: string; paid_hours: number; days_off: number; days: ScheduleDay[] }

export type AgentSchedule = {
  agent_id: string
  source: 'mock' | 'baseline' | 'optimized'
  validation_status: string
  horizon_start: string
  weeks: ScheduleWeek[]
}

export type AgentScheduleResponse = { agent: AgentRecord; schedule: AgentSchedule }

export async function fetchAgentSchedule(agentId: string): Promise<AgentScheduleResponse> {
  const response = await fetch(`/api/agents/${encodeURIComponent(agentId)}/schedule`)
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load schedule'))
  return (await response.json()) as AgentScheduleResponse
}
