import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { GoogleOAuthProvider } from '@react-oauth/google'

import '@xyflow/react/dist/style.css'
import './index.css'
import App from './App'
import { ToastProvider } from './lib/toast'

const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID || ''
const app = (
  <ToastProvider>
    <App />
  </ToastProvider>
)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {clientId ? <GoogleOAuthProvider clientId={clientId}>{app}</GoogleOAuthProvider> : app}
  </StrictMode>,
)
