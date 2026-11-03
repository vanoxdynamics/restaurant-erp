import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ApiError } from './api';
import { App } from './App';
import { AuthProvider, StepUpProvider } from './auth';
import './styles.css';
import { ToastProvider } from './ui';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      // Retrying a 4xx never helps (and would repeat rate-limited or forbidden calls).
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthProvider>
          <StepUpProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </StepUpProvider>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
