import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import App from './App.tsx'

test('renders the app title', () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  render(
    <MantineProvider>
      <QueryClientProvider client={new QueryClient()}>
        <App />
      </QueryClientProvider>
    </MantineProvider>,
  )
  expect(screen.getByText('Workforce Management POC')).toBeInTheDocument()
})
