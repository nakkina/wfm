import { Badge, Group, Text, Tree, useTree, type TreeNodeData } from '@mantine/core'
import { useMemo } from 'react'

import type { Organization } from '../api/hierarchy.ts'

export type NodeKind = 'org' | 'bu' | 'mu' | 'queue'

/** A selected hierarchy node plus its ancestors' names, for breadcrumbs. */
export type HierarchySelection = {
  kind: NodeKind
  code: string
  name: string
  path: string[]
  agentCount: number
}

const ROOT_VALUE = 'org'

function buildTree(org: Organization): {
  data: TreeNodeData[]
  byValue: Map<string, HierarchySelection>
} {
  const byValue = new Map<string, HierarchySelection>()
  const add = (value: string, selection: HierarchySelection) => byValue.set(value, selection)
  add(ROOT_VALUE, { kind: 'org', code: '', name: org.name, path: [], agentCount: org.agent_count })

  const data: TreeNodeData[] = [
    {
      value: ROOT_VALUE,
      label: org.name,
      children: org.business_units.map((bu) => {
        const buPath = [org.name]
        add(bu.code, { kind: 'bu', code: bu.code, name: bu.name, path: buPath, agentCount: bu.agent_count })
        return {
          value: bu.code,
          label: bu.name,
          children: bu.management_units.map((mu) => {
            const muPath = [org.name, bu.name]
            add(mu.code, { kind: 'mu', code: mu.code, name: mu.name, path: muPath, agentCount: mu.agent_count })
            return {
              value: mu.code,
              label: mu.name,
              children: mu.queues.map((q) => {
                const qPath = [...muPath, mu.name]
                add(q.id, { kind: 'queue', code: q.id, name: q.name, path: qPath, agentCount: q.agent_count })
                return { value: q.id, label: q.name }
              }),
            }
          }),
        }
      }),
    },
  ]
  return { data, byValue }
}

type Props = {
  org: Organization
  onSelect: (selection: HierarchySelection) => void
}

export function HierarchyTree({ org, onSelect }: Props) {
  const { data, byValue } = useMemo(() => buildTree(org), [org])
  const tree = useTree({
    // Root and business units open; management units collapsed until clicked.
    initialExpandedState: Object.fromEntries(
      [ROOT_VALUE, ...org.business_units.map((bu) => bu.code)].map((v) => [v, true]),
    ),
    onSelectedStateChange: (selected) => {
      const selection = selected[0] ? byValue.get(selected[0]) : undefined
      if (selection) onSelect(selection)
    },
  })

  return (
    <Tree
      data={data}
      tree={tree}
      selectOnClick
      levelOffset="md"
      aria-label="Organization hierarchy"
      renderNode={({ node, expanded, hasChildren, selected, elementProps }) => {
        const info = byValue.get(node.value)
        const isGroup = info?.kind === 'bu' || info?.kind === 'mu'
        return (
          <Group
            gap={6}
            wrap="nowrap"
            py={3}
            pr={6}
            {...elementProps}
            style={{
              ...elementProps.style,
              borderRadius: 4,
              cursor: 'pointer',
              background: selected ? 'var(--mantine-color-blue-light)' : undefined,
            }}
          >
            <Text size="xs" c="dimmed" w={10} aria-hidden>
              {hasChildren ? (expanded ? '▾' : '▸') : '•'}
            </Text>
            <Text size="sm" fw={info?.kind === 'queue' ? 400 : 600} truncate="end" title={String(node.label)}>
              {node.label}
            </Text>
            {isGroup && (
              <Badge size="xs" variant="light" color="gray" style={{ flexShrink: 0 }}>
                {info.code}
              </Badge>
            )}
            {info && (
              <Text
                size="xs"
                c="dimmed"
                ml="auto"
                pl={4}
                style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}
                aria-label={`${info.agentCount} agents`}
              >
                {info.agentCount}
              </Text>
            )}
          </Group>
        )
      }}
    />
  )
}
