import '@mantine/core/styles.css'

import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AllCommunityModule, ModuleRegistry } from 'ag-grid-community'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.tsx'

ModuleRegistry.registerModules([AllCommunityModule])

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
)
