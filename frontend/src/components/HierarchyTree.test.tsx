import { MantineProvider } from '@mantine/core'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import type { Organization } from '../api/hierarchy.ts'
import { HierarchyTree } from './HierarchyTree.tsx'

const org: Organization = {
  name: 'Test Center',
  agent_count: 7,
  business_units: [
    {
      code: 'BU-1',
      name: 'Banking',
      agent_count: 7,
      management_units: [
        {
          code: 'MU-1',
          name: 'Deposits',
          agent_count: 7,
          queues: [{ id: 'Q-1', name: 'Checking Support', agent_count: 7 }],
        },
      ],
    },
  ],
}

function renderTree(onSelect = vi.fn()) {
  render(
    <MantineProvider>
      <HierarchyTree org={org} onSelect={onSelect} />
    </MantineProvider>,
  )
  return onSelect
}

test('shows business units expanded and management units collapsed', () => {
  renderTree()
  expect(screen.getByText('Banking')).toBeInTheDocument()
  expect(screen.getByText('Deposits')).toBeInTheDocument()
  expect(screen.getByText('MU-1')).toBeInTheDocument()
  expect(screen.queryByText('Checking Support')).not.toBeInTheDocument()
})

test('shows agent counts on each visible node', () => {
  renderTree()
  // Root, BU and MU are visible (queue collapsed); all roll up to 7.
  expect(screen.getAllByLabelText('7 agents')).toHaveLength(3)
})

test('expanding a management unit reveals queues, and selecting a queue reports its path', () => {
  const onSelect = renderTree()
  fireEvent.click(screen.getByText('Deposits'))
  expect(onSelect).toHaveBeenLastCalledWith(
    expect.objectContaining({ kind: 'mu', code: 'MU-1', path: ['Test Center', 'Banking'] }),
  )
  fireEvent.click(screen.getByText('Checking Support'))
  expect(onSelect).toHaveBeenLastCalledWith({
    kind: 'queue',
    code: 'Q-1',
    name: 'Checking Support',
    path: ['Test Center', 'Banking', 'Deposits'],
    agentCount: 7,
  })
})
