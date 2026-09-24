import { beforeEach, expect, test } from 'vitest'

import type { ColumnInfo } from '../api/agents.ts'
import {
  DEFAULT_COLUMNS,
  fieldLabel,
  formatValue,
  loadColumns,
  saveColumns,
  toggleColumn,
} from './agentColumns.ts'

const list: ColumnInfo = { field: 'languages', category: 'Skills', type: 'semicolon list', poc_use: '', description: '' }

beforeEach(() => localStorage.clear())

test('fieldLabel turns snake_case into a sentence-case label with acronyms', () => {
  expect(fieldLabel('weekly_contracted_hours')).toBe('Weekly contracted hours')
  expect(fieldLabel('bu_id')).toBe('BU ID')
  expect(fieldLabel('agent_id')).toBe('Agent ID')
  expect(fieldLabel('csat_pct')).toBe('CSAT %')
})

test('formatValue handles lists, booleans and blanks', () => {
  expect(formatValue('English;Spanish', list)).toBe('English, Spanish')
  expect(formatValue(true, undefined)).toBe('Yes')
  expect(formatValue(null, undefined)).toBe('')
  expect(formatValue(0, undefined)).toBe('0')
})

test('toggleColumn appends new fields and removes shown ones', () => {
  expect(toggleColumn(['a', 'b'], 'c')).toEqual(['a', 'b', 'c'])
  expect(toggleColumn(['a', 'b', 'c'], 'b')).toEqual(['a', 'c'])
})

test('agent ID cannot be removed, and is restored if a saved choice lacks it', () => {
  expect(toggleColumn(['agent_id', 'role'], 'agent_id')).toEqual(['agent_id', 'role'])
  saveColumns(['role', 'csat_pct'])
  expect(loadColumns(['agent_id', 'role', 'csat_pct'])).toEqual(['agent_id', 'role', 'csat_pct'])
})

test('loadColumns falls back to defaults that exist in the Dictionary', () => {
  expect(loadColumns(['agent_id', 'agent_name', 'csat_pct'])).toEqual(['agent_id', 'agent_name'])
  expect(loadColumns(DEFAULT_COLUMNS)).toEqual(DEFAULT_COLUMNS)
})

test('loadColumns restores saved order and drops unknown fields', () => {
  saveColumns(['csat_pct', 'removed_field', 'agent_id'])
  expect(loadColumns(['agent_id', 'csat_pct'])).toEqual(['csat_pct', 'agent_id'])
})

test('loadColumns ignores corrupt storage', () => {
  localStorage.setItem('wfm.agentTable.columns', '{not json')
  expect(loadColumns(['agent_id'])).toEqual(['agent_id'])
})
