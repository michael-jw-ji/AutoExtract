import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import data from './data.json'
import type { SiteData } from './types'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App data={data as SiteData} />
  </StrictMode>,
)
