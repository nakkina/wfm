import { AppShell, Badge, Group, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

type Health = { status: string; version: string }

async function fetchHealth(): Promise<Health> {
  const response = await fetch('/api/health')
  if (!response.ok) throw new Error(`Health check failed: ${response.status}`)
  return (await response.json()) as Health
}

export default function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth })

  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={3}>Workforce Management POC</Title>
          <Badge color={health.isSuccess ? 'green' : health.isError ? 'red' : 'gray'}>
            API: {health.isSuccess ? health.data.status : health.isError ? 'unreachable' : 'checking'}
          </Badge>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Text c="dimmed">Scaffold only — the workspace UI is PRD build step 1.</Text>
      </AppShell.Main>
    </AppShell>
  )
}
