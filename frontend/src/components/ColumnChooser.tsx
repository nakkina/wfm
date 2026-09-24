import { Button, Checkbox, Divider, Group, Popover, ScrollArea, Stack, Text, Tooltip } from '@mantine/core'
import { useMemo } from 'react'

import type { ColumnInfo } from '../api/agents.ts'
import { AGENT_ID, fieldLabel, toggleColumn } from './agentColumns.ts'

type Props = {
  columns: ColumnInfo[]
  selected: string[]
  onChange: (selected: string[]) => void
  onReset: () => void
}

/** Checkbox list of every Dictionary field, grouped by category. Order is changed in the grid. */
export function ColumnChooser({ columns, selected, onChange, onReset }: Props) {
  const groups = useMemo(() => {
    const byCategory = new Map<string, ColumnInfo[]>()
    for (const c of columns) byCategory.set(c.category, [...(byCategory.get(c.category) ?? []), c])
    return [...byCategory.entries()]
  }, [columns])

  return (
    <Popover position="bottom-end" width={340} shadow="md" withinPortal>
      <Popover.Target>
        <Button variant="default" size="xs">
          Columns ({selected.length}/{columns.length})
        </Button>
      </Popover.Target>
      <Popover.Dropdown p="xs">
        <Group justify="space-between" mb={4}>
          <Text size="xs" c="dimmed">
            Drag column headers to reorder
          </Text>
          <Button variant="subtle" size="compact-xs" onClick={onReset}>
            Reset to default
          </Button>
        </Group>
        <ScrollArea.Autosize mah={420} type="auto">
          <Stack gap={4}>
            {groups.map(([category, fields]) => (
              <div key={category}>
                <Divider label={category} labelPosition="left" my={4} />
                <Stack gap={4}>
                  {fields.map((c) => (
                    <Tooltip
                      key={c.field}
                      label={c.field === AGENT_ID ? 'Always shown — opens the agent schedule' : c.description}
                      multiline
                      w={280}
                      openDelay={400}
                    >
                      <Checkbox
                        size="xs"
                        label={fieldLabel(c.field)}
                        checked={selected.includes(c.field)}
                        disabled={c.field === AGENT_ID}
                        onChange={() => onChange(toggleColumn(selected, c.field))}
                      />
                    </Tooltip>
                  ))}
                </Stack>
              </div>
            ))}
          </Stack>
        </ScrollArea.Autosize>
      </Popover.Dropdown>
    </Popover>
  )
}
