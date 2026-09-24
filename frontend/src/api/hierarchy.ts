export type Queue = { id: string; name: string; agent_count: number }
export type ManagementUnit = { code: string; name: string; queues: Queue[]; agent_count: number }
export type BusinessUnit = {
  code: string
  name: string
  management_units: ManagementUnit[]
  agent_count: number
}
export type Organization = { name: string; business_units: BusinessUnit[]; agent_count: number }

export async function fetchHierarchy(): Promise<Organization> {
  const response = await fetch('/api/hierarchy')
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load hierarchy'))
  return (await response.json()) as Organization
}

/** Uses FastAPI's `detail` message when present, so validation errors reach the UI. */
export async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Non-JSON error body; fall through.
  }
  return `${fallback}: ${response.status}`
}
