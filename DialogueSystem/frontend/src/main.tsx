import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

if (import.meta.hot?.on) {
  import.meta.hot.on('vite:error', (error) => {
    if (error.err) {
      console.error([error.err.message, error.err.frame].filter(Boolean).join('\n'))
    }
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
