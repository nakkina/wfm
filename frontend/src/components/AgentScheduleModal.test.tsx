import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import type { AgentScheduleResponse, ScheduleDay } from '../api/schedule.ts'
import { AgentScheduleModal } from './AgentScheduleModal.tsx'

function workDay(date: string, weekday: string, start: string): ScheduleDay {
  const [h] = start.split(':').map(Number)
  const t = (hour: number, min = 0) => `${String(hour).padStart(2, '0')}:${String(min).padStart(2, '0')}:00`
  return {
    date,
    weekday,
    off: false,
    shift_code: 's1',
    shift_name: 'Early',
    start: t(h),
    end: t(h + 8, 30),
    paid_hours: 8,
    segments: [
      { kind: 'work', start: t(h), end: t(h + 2) },
      { kind: 'break', start: t(h + 2), end: t(h + 2, 15) },
      { kind: 'work', start: t(h + 2, 15), end: t(h + 4) },
      { kind: 'meal', start: t(h + 4), end: t(h + 4, 30) },
      { kind: 'work', start: t(h + 4, 30), end: t(h + 8, 30) },
    ],
  }
}

function week(start: number, firstShift: string) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((weekday, i) => {
    const date = `2026-${start + i > 30 ? '10' : '09'}-${String(((start + i - 1) % 30) + 1).padStart(2, '0')}`
    return i >= 5 ? { ...workDay(date, weekday, firstShift), off: true, segments: [], paid_hours: 0 } : workDay(date, weekday, firstShift)
  })
  return { week_start: days[0].date, paid_hours: 40, days_off: 2, days }
}

const response: AgentScheduleResponse = {
  agent: { agent_id: 'AG0012', agent_name: 'Yusuf Haddad', queue_name: 'Card Purchase Disputes', work_plan_id: 'WP-FT', languages: 'English;Spanish' },
  schedule: {
    agent_id: 'AG0012',
    source: 'mock',
    validation_status: 'Mock data — not validated',
    horizon_start: '2026-09-28',
    weeks: [week(28, '08:00'), week(35, '10:00')],
  },
}

function renderModal() {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(response))))
  render(
    <MantineProvider>
      <QueryClientProvider client={new QueryClient()}>
        <AgentScheduleModal agentId="AG0012" onClose={() => {}} />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

test('shows the agent profile, mock label and first week', async () => {
  renderModal()
  expect(await screen.findByText('Yusuf Haddad')).toBeInTheDocument()
  expect(screen.getByText('MOCK')).toBeInTheDocument()
  expect(screen.getByText('English, Spanish')).toBeInTheDocument()
  expect(screen.getByLabelText('Mon Sep 28: Early 08:00–16:30')).toBeInTheDocument()
  expect(screen.getByLabelText('Sat Oct 3: day off')).toBeInTheDocument()
  // First working day's timeline is shown by default, with text labels for each segment.
  expect(screen.getByRole('img', { name: /Work 08:00–10:00, Break 10:00–10:15/ })).toBeInTheDocument()
})

test('switches weeks and selected day', async () => {
  renderModal()
  await screen.findByText('Yusuf Haddad')
  fireEvent.click(screen.getByText('Week 2'))
  expect(screen.getByLabelText('Mon Oct 5: Early 10:00–18:30')).toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('Wed Oct 7: Early 10:00–18:30'))
  expect(screen.getByText(/Wed Oct 7 · Early shift 10:00–18:30/)).toBeInTheDocument()
})
