import type { ColumnInfo } from '../api/agents.ts'

/** Always shown: its cells link to the agent's schedule. */
export const AGENT_ID = 'agent_id'

/** Columns shown until the user picks their own (PRD §3: ID/name, skills, languages, plan, hours). */
export const DEFAULT_COLUMNS = [
  'agent_id',
  'agent_name',
  'team_id',
  'role',
  'employment_type',
  'primary_skill',
  'skill_proficiency',
  'languages',
  'work_plan_id',
  'weekly_contracted_hours',
  'allowed_shifts',
]

const STORAGE_KEY = 'wfm.agentTable.columns'

const WORD_LABELS: Record<string, string> = {
  id: 'ID',
  bu: 'BU',
  mu: 'MU',
  csat: 'CSAT',
  fte: 'FTE',
  pct: '%',
}

/** "weekly_contracted_hours" -> "Weekly contracted hours"; "bu_id" -> "BU ID"; "csat_pct" -> "CSAT %" */
export function fieldLabel(field: string): string {
  const words = field.split('_').map((w) => WORD_LABELS[w] ?? w)
  const label = words.join(' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

export function isNumeric(column: ColumnInfo): boolean {
  return /^(number|integer)/.test(column.type)
}

export function formatValue(value: unknown, column: ColumnInfo | undefined): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (column?.type === 'semicolon list' && typeof value === 'string') return value.split(';').join(', ')
  return String(value)
}

/** Adds a field at the end, or removes it if already shown. Agent ID can't be removed. */
export function toggleColumn(columns: string[], field: string): string[] {
  if (field === AGENT_ID) return columns
  return columns.includes(field) ? columns.filter((c) => c !== field) : [...columns, field]
}

/** Saved column choice, dropping fields the current Dictionary no longer has. */
export function loadColumns(available: string[]): string[] {
  const known = new Set(available)
  const defaults = DEFAULT_COLUMNS.filter((f) => known.has(f))
  let chosen = defaults
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as unknown
    if (Array.isArray(saved)) {
      const valid = saved.filter((f): f is string => typeof f === 'string' && known.has(f))
      if (valid.length > 0) chosen = valid
    }
  } catch {
    // Storage unavailable or corrupt: fall back to defaults.
  }
  // Older saved choices may have dropped Agent ID; put it back first.
  return known.has(AGENT_ID) && !chosen.includes(AGENT_ID) ? [AGENT_ID, ...chosen] : chosen
}

export function saveColumns(columns: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(columns))
  } catch {
    // Storage unavailable (private window, blocked site data): keep the in-memory choice only.
  }
}

export function clearSavedColumns(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing to clear.
  }
}
