import { Alert, Anchor, Group, Loader, Stack, Text, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { themeQuartz, type ColDef, type DragStoppedEvent } from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import { useMemo, useState } from 'react'

import { fetchQueueAgents, type AgentRecord, type ColumnInfo } from '../api/agents.ts'
import {
  AGENT_ID,
  clearSavedColumns,
  DEFAULT_COLUMNS,
  fieldLabel,
  formatValue,
  isNumeric,
  loadColumns,
  saveColumns,
} from './agentColumns.ts'
import { ColumnChooser } from './ColumnChooser.tsx'

const gridTheme = themeQuartz.withParams({ fontSize: 13, headerFontSize: 13, spacing: 6 })

type Props = { queueId: string; columns: ColumnInfo[]; onOpenAgent: (agentId: string) => void }

export function AgentTable({ queueId, columns, onOpenAgent }: Props) {
  const agents = useQuery({
    queryKey: ['queue-agents', queueId],
    queryFn: () => fetchQueueAgents(queueId),
  })
  const [search, setSearch] = useState('')
  const [shown, setShown] = useState(() => loadColumns(columns.map((c) => c.field)))

  const byField = useMemo(() => new Map(columns.map((c) => [c.field, c])), [columns])

  const columnDefs = useMemo<ColDef<AgentRecord>[]>(
    () =>
      shown.map((field) => {
        const info = byField.get(field)
        return {
          colId: field,
          field,
          headerName: fieldLabel(field),
          headerTooltip: info?.description,
          type: info && isNumeric(info) ? 'numericColumn' : undefined,
          valueFormatter: ({ value }) => formatValue(value, info),
          getQuickFilterText: ({ value }) => formatValue(value, info),
          minWidth: 90,
          ...(field === AGENT_ID && {
            cellRenderer: ({ value }: { value: unknown }) => (
              <Anchor
                component="button"
                size="sm"
                onClick={() => onOpenAgent(String(value))}
                aria-label={`Open schedule for ${String(value)}`}
              >
                {String(value)}
              </Anchor>
            ),
          }),
        }
      }),
    [shown, byField, onOpenAgent],
  )

  function updateShown(next: string[]) {
    setShown(next)
    saveColumns(next)
  }

  function onDragStopped(event: DragStoppedEvent<AgentRecord>) {
    const order = event.api
      .getColumnState()
      .filter((s) => !s.hide)
      .map((s) => s.colId)
    if (order.join() !== shown.join()) updateShown(order)
  }

  return (
    <Stack gap="xs" style={{ flex: 1, minHeight: 0 }}>
      <Group justify="space-between">
        <TextInput
          size="xs"
          w={280}
          placeholder="Search agents..."
          aria-label="Search agents"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
        />
        <ColumnChooser
          columns={columns}
          selected={shown}
          onChange={updateShown}
          onReset={() => {
            clearSavedColumns()
            setShown(DEFAULT_COLUMNS.filter((f) => byField.has(f)))
          }}
        />
      </Group>

      {agents.isPending && <Loader size="sm" />}
      {agents.isError && (
        <Alert color="red" title="Could not load agents">
          {agents.error.message}
        </Alert>
      )}
      {agents.isSuccess && agents.data.length === 0 && (
        <Text c="dimmed" size="sm">
          No agents are assigned to this queue.
        </Text>
      )}
      {agents.isSuccess && agents.data.length > 0 && (
        <div style={{ flex: 1, minHeight: 300 }}>
          <AgGridReact<AgentRecord>
            theme={gridTheme}
            rowData={agents.data}
            columnDefs={columnDefs}
            defaultColDef={{ sortable: true, resizable: true, filter: false }}
            getRowId={({ data }) => String(data.agent_id)}
            quickFilterText={search}
            onDragStopped={onDragStopped}
            autoSizeStrategy={{ type: 'fitCellContents' }}
            tooltipShowDelay={400}
          />
        </div>
      )}
    </Stack>
  )
}
