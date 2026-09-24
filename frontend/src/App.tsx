import {
  Alert,
  AppShell,
  Badge,
  Breadcrumbs,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Tabs,
  Text,
  Title,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { fetchAgentColumns } from './api/agents.ts'
import { fetchHierarchy } from './api/hierarchy.ts'
import { AgentScheduleModal } from './components/AgentScheduleModal.tsx'
import { AgentTable } from './components/AgentTable.tsx'
import { ForecastCharts } from './components/ForecastCharts.tsx'
import { HierarchyTree, type HierarchySelection } from './components/HierarchyTree.tsx'

type QueueTab = 'agents' | 'forecasts'

// Tab panels fill the remaining height so the grid and charts can scroll inside them.
const PANEL_STYLE = { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' } as const

type Health = { status: string; version: string }

async function fetchHealth(): Promise<Health> {
  const response = await fetch('/api/health')
  if (!response.ok) throw new Error(`Health check failed: ${response.status}`)
  return (await response.json()) as Health
}

const KIND_LABEL = { org: 'Organization', bu: 'Business unit', mu: 'Management unit', queue: 'Queue' }

export default function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth })
  const hierarchy = useQuery({ queryKey: ['hierarchy'], queryFn: fetchHierarchy })
  const columns = useQuery({ queryKey: ['agent-columns'], queryFn: fetchAgentColumns })
  const [selection, setSelection] = useState<HierarchySelection | null>(null)
  // Kept when switching queues, so comparing forecasts across queues stays on that tab.
  const [tab, setTab] = useState<QueueTab>('agents')
  const [openAgentId, setOpenAgentId] = useState<string | null>(null)

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 420, breakpoint: 'sm' }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={3}>Workforce Management POC</Title>
          <Badge color={health.isSuccess ? 'green' : health.isError ? 'red' : 'gray'}>
            API: {health.isSuccess ? health.data.status : health.isError ? 'unreachable' : 'checking'}
          </Badge>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <Text size="xs" fw={700} c="dimmed" tt="uppercase" px={6} pb={6}>
          Organization
        </Text>
        <AppShell.Section grow component={ScrollArea}>
          {hierarchy.isPending && <Loader size="sm" m="sm" />}
          {hierarchy.isError && (
            <Alert color="red" title="Could not load hierarchy">
              {hierarchy.error.message}
            </Alert>
          )}
          {hierarchy.isSuccess && <HierarchyTree org={hierarchy.data} onSelect={setSelection} />}
        </AppShell.Section>
      </AppShell.Navbar>

      {/* Main fills the viewport below the header so the grid can take the remaining height. */}
      <AppShell.Main style={{ display: 'flex', flexDirection: 'column', height: '100dvh' }}>
        {selection ? (
          <Stack gap="sm" style={{ flex: 1, minHeight: 0 }}>
            <Stack gap={4}>
              {selection.path.length > 0 && (
                <Breadcrumbs separator="›">
                  {selection.path.map((name) => (
                    <Text key={name} size="sm" c="dimmed">
                      {name}
                    </Text>
                  ))}
                </Breadcrumbs>
              )}
              <Group gap="xs">
                <Title order={4}>{selection.name}</Title>
                {selection.code && <Badge variant="light">{selection.code}</Badge>}
                <Text size="sm" c="dimmed">
                  {selection.agentCount} agents
                </Text>
              </Group>
            </Stack>

            {selection.kind !== 'queue' ? (
              <Text size="sm" c="dimmed">
                {KIND_LABEL[selection.kind]} — select a queue to see its agents and forecasts.
              </Text>
            ) : (
              <Tabs
                value={tab}
                onChange={(v) => setTab(v === 'forecasts' ? 'forecasts' : 'agents')}
                keepMounted={false}
                style={PANEL_STYLE}
              >
                <Tabs.List>
                  <Tabs.Tab value="agents">Agents</Tabs.Tab>
                  <Tabs.Tab value="forecasts">Forecasts</Tabs.Tab>
                </Tabs.List>

                <Tabs.Panel value="agents" pt="sm" style={PANEL_STYLE}>
                  {columns.isError ? (
                    <Alert color="red" title="Could not load agent columns">
                      {columns.error.message}
                    </Alert>
                  ) : columns.isSuccess ? (
                    // key resets search when switching queues; column choice persists via storage.
                    <AgentTable
                      key={selection.code}
                      queueId={selection.code}
                      columns={columns.data}
                      onOpenAgent={setOpenAgentId}
                    />
                  ) : (
                    <Loader size="sm" />
                  )}
                </Tabs.Panel>

                <Tabs.Panel value="forecasts" pt="sm" style={PANEL_STYLE}>
                  <ForecastCharts key={selection.code} queueId={selection.code} />
                </Tabs.Panel>
              </Tabs>
            )}
          </Stack>
        ) : (
          <Text c="dimmed">Select a business unit, management unit or queue.</Text>
        )}
      </AppShell.Main>

      <AgentScheduleModal agentId={openAgentId} onClose={() => setOpenAgentId(null)} />
    </AppShell>
  )
}
