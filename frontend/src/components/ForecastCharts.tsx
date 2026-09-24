import { Alert, Badge, Group, Loader, Paper, ScrollArea, Stack, Text, Tooltip } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import type { Data, Layout } from 'plotly.js'
import { lazy, Suspense } from 'react'

import { fetchQueueForecast, type TargetForecast } from '../api/forecast.ts'
import {
  eventMarkers,
  forecastBands,
  forecastStartMarker,
  historySeries,
  predictabilityNote,
  scoreSummary,
  type EventMarkers,
  type Series,
} from './historySeries.ts'
import { shortDate } from './scheduleUtils.ts'

// Plotly is ~4 MB; load it only when this tab is first opened.
const Plot = lazy(async () => {
  const [{ default: createPlotlyComponent }, plotly] = await Promise.all([
    import('react-plotly.js/factory'),
    import('plotly.js-dist-min'),
  ])
  return { default: createPlotlyComponent(plotly.default) }
})

const COLORS = { volume: '34, 139, 230', aht: '232, 89, 12' } // RGB, reused with alpha for bands
const BAND_ALPHA: Record<number, number> = { 95: 0.12, 80: 0.25 }

export function ForecastCharts({ queueId }: { queueId: string }) {
  // Served from the saved forecast run; it only changes when a new run is generated,
  // so keep it for the session instead of refetching on every visit.
  const query = useQuery({
    queryKey: ['queue-forecast', queueId],
    queryFn: () => fetchQueueForecast(queueId),
    staleTime: Infinity,
  })

  if (query.isPending)
    return (
      <Stack align="center" p="xl">
        <Loader size="sm" />
        <Text size="sm" c="dimmed" ta="center">
          Loading the saved forecast. If none exists for the current ACD file, one is generated for
          all queues first (about 1–2 minutes, once).
        </Text>
      </Stack>
    )
  if (query.isError)
    return (
      <Alert color="red" title="Could not load forecast">
        {query.error.message}
      </Alert>
    )

  const fc = query.data
  const { volume, aht } = historySeries(fc.history)
  const forecastEnd = (fc.volume ?? fc.aht)?.points.at(-1)?.date

  return (
    <ScrollArea style={{ flex: 1 }} offsetScrollbars>
      <Stack gap="sm">
        <Group gap="xs">
          <Text size="sm" c="dimmed">
            Daily actuals {shortDate(fc.history_start)} – {shortDate(fc.history_end)}
            {forecastEnd && `, forecast ${shortDate(fc.horizon_start)} – ${shortDate(forecastEnd)} (${fc.horizon_days} days)`}
          </Text>
          <Tooltip label={fc.interval_method} multiline w={320}>
            <Badge variant="light" color="gray">
              Intervals: {fc.levels.join('% / ')}%
            </Badge>
          </Tooltip>
          <Tooltip label={`Saved forecast run ${fc.run_id}`}>
            <Badge variant="light" color="gray">
              Run {shortDate(fc.created_at.slice(0, 10))}
            </Badge>
          </Tooltip>
        </Group>
        {fc.warnings.map((w) => (
          <Alert key={w} color="yellow" title="Data warning">
            {w}
          </Alert>
        ))}

        <Suspense fallback={<Loader size="sm" />}>
          <TrendChart
            title="Call volume (calls offered per day)"
            history={volume}
            events={eventMarkers(fc.history, volume)}
            forecast={fc.volume}
            historyEnd={fc.history_end}
            yTitle="Calls offered"
            valueFormat=",.0f"
            unit=" calls"
            rgb={COLORS.volume}
            fromZero
          />
          <TrendChart
            title="Average handle time (handle seconds ÷ handled calls, per day)"
            history={aht}
            events={eventMarkers(fc.history, aht)}
            forecast={fc.aht}
            historyEnd={fc.history_end}
            yTitle="AHT (seconds)"
            valueFormat=".0f"
            unit=" s"
            rgb={COLORS.aht}
          />
        </Suspense>
      </Stack>
    </ScrollArea>
  )
}

type ChartProps = {
  title: string
  history: Series
  events: EventMarkers
  forecast: TargetForecast | null
  historyEnd: string
  yTitle: string
  valueFormat: string
  unit: string
  rgb: string
  fromZero?: boolean // counts start at zero; AHT uses a tighter range so variation is visible
}

function TrendChart({
  title,
  history,
  events,
  forecast,
  historyEnd,
  yTitle,
  valueFormat,
  unit,
  rgb,
  fromZero = false,
}: ChartProps) {
  const traces: Data[] = [
    {
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Actual',
      x: history.dates,
      y: history.values,
      line: { color: `rgb(${rgb})`, width: 2 },
      marker: { size: 4 },
      connectgaps: false,
      hovertemplate: `Actual: %{y:${valueFormat}}${unit}<extra></extra>`,
    },
  ]

  if (events.dates.length > 0) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Event day (excluded from training)',
      x: events.dates,
      y: events.values,
      marker: { symbol: 'x', size: 9, color: 'rgb(60, 60, 60)' },
      customdata: events.labels,
      hovertemplate: 'Event: %{customdata}<extra></extra>',
    })
  }

  if (forecast) {
    for (const band of forecastBands(forecast.points)) {
      const fill = `rgba(${rgb}, ${BAND_ALPHA[band.level] ?? 0.15})`
      // Upper edge first (invisible), then the lower edge filled up to it ("tonexty").
      traces.push(
        {
          type: 'scatter',
          mode: 'lines',
          x: band.dates,
          y: band.hi,
          line: { width: 0 },
          showlegend: false,
          hoverinfo: 'skip',
        },
        {
          type: 'scatter',
          mode: 'lines',
          name: `${band.level}% interval`,
          x: band.dates,
          y: band.lo,
          fill: 'tonexty',
          fillcolor: fill,
          line: { width: 0 },
          customdata: band.hi,
          hovertemplate: `${band.level}%: %{y:${valueFormat}}–%{customdata:${valueFormat}}${unit}<extra></extra>`,
        },
      )
    }
    traces.push({
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Forecast',
      x: forecast.points.map((p) => p.date),
      y: forecast.points.map((p) => p.value),
      line: { color: `rgb(${rgb})`, width: 2, dash: 'dash' },
      marker: { size: 4, symbol: 'circle-open' },
      hovertemplate: `Forecast: %{y:${valueFormat}}${unit}<extra></extra>`,
    })
  }

  const marker = forecastStartMarker(historyEnd)
  const layout: Partial<Layout> = {
    height: 300,
    margin: { l: 60, r: 16, t: 8, b: 40 },
    xaxis: { type: 'date', tickformat: '%b %d', showgrid: true },
    yaxis: { title: { text: yTitle }, rangemode: fromZero ? 'tozero' : 'normal', separatethousands: true },
    legend: { orientation: 'h', x: 0, y: 1.12 },
    hovermode: 'x unified',
    shapes: [
      {
        type: 'line',
        xref: 'x',
        yref: 'paper',
        x0: marker,
        x1: marker,
        y0: 0,
        y1: 1,
        line: { color: 'rgba(80, 80, 80, 0.8)', width: 1.5, dash: 'dot' },
      },
    ],
    annotations: [
      {
        x: marker,
        xref: 'x',
        yref: 'paper',
        y: 1,
        yanchor: 'bottom',
        xanchor: 'left',
        text: 'Forecast start',
        showarrow: false,
        font: { size: 11, color: '#555' },
      },
    ],
  }

  return (
    <Paper withBorder p="xs">
      <Group justify="space-between" px={4} gap="xs">
        <Text size="sm" fw={600}>
          {title}
        </Text>
        {forecast && (
          <Tooltip
            multiline
            w={360}
            label={forecast.scores.map((s) => `${s.chosen ? '✓ ' : ''}${s.label}: ${s.score}`).join('\n')}
            style={{ whiteSpace: 'pre-line' }}
          >
            <Text size="xs" c="dimmed">
              Model: {forecast.model_label} · {scoreSummary(forecast)}
            </Text>
          </Tooltip>
        )}
      </Group>
      <Plot
        data={traces}
        layout={layout}
        config={{ displaylogo: false, responsive: true }}
        useResizeHandler
        style={{ width: '100%' }}
      />
      {forecast?.diagnostics && (
        <Text size="xs" c="dimmed" px={4}>
          {predictabilityNote(forecast.diagnostics)}
        </Text>
      )}
    </Paper>
  )
}
