import React, { Suspense, lazy, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { loadAppShell, type AppShellProps } from './components/appShells';
import { useAppShell } from './components/useAppShell';
import { ChatSessionProvider } from './contexts/ChatSessionContext';
import { PageContextProvider } from './contexts/PageContextProvider';
import { UIActionProvider } from './contexts/UIActionProvider';
import { useDomainChangeSync } from './hooks/useDomainChangeSync';
import { WhatsNewFlash } from './components/features/WhatsNewFlash';
import { UIVersionProvider } from './contexts/UIVersionContext';
import { useSyncUIVersionFromServer, useUIVersion, type UIVersion } from './contexts/uiVersion';
import { authApi } from './api';
import { setupStatusQueryOptions } from './lib/setupStatus';
import { withStaleChunkReload } from './lib/staleChunkReload';
import { ErrorBoundary } from './components/ui/ErrorBoundary';

// Route-level code splitting (JAN-179): each page is its own chunk, fetched the first
// time its route renders. React Router wraps navigations in a transition, so moving to a
// not-yet-loaded page keeps the current one on screen until the chunk arrives. A chunk a
// deploy removed reloads the tab into the new build instead of crashing it.
const ChatPage = lazy(withStaleChunkReload(() => import('./pages/ChatPage')));
const Dashboard = lazy(withStaleChunkReload(() => import('./pages/Dashboard')));
const Login = lazy(withStaleChunkReload(() => import('./pages/Login')));
const Emails = lazy(withStaleChunkReload(() => import('./pages/Emails')));
const PlannerPage = lazy(withStaleChunkReload(() => import('./pages/PlannerPage')));
const Settings = lazy(withStaleChunkReload(() => import('./pages/Settings')));
const MorningBriefing = lazy(withStaleChunkReload(() => import('./pages/MorningBriefing')));
const MemoryPage = lazy(withStaleChunkReload(() => import('./pages/MemoryPage')));
const TasksPage = lazy(withStaleChunkReload(() => import('./pages/TasksPage')));
const Documentation = lazy(withStaleChunkReload(() => import('./pages/Documentation')));
const Setup = lazy(withStaleChunkReload(() => import('./pages/Setup')));

const queryClient = new QueryClient();

const FullScreenLoader = () => (
  <div className="flex items-center justify-center h-screen bg-light-bg dark:bg-dark-bg">
    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
  </div>
);

/** Lightweight in-shell placeholder while a page chunk loads for the first time. */
const PageLoader = () => (
  <div className="flex items-center justify-center py-24" role="status" aria-label="Loading page">
    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
  </div>
);

interface AppShellHostProps extends AppShellProps {
  version: UIVersion;
}

/** Renders the lazily loaded shell for `version` around the routed page. */
const AppShellHost: React.FC<AppShellHostProps> = ({ version, currentUser, children }) => {
  const Shell = useAppShell(version);
  if (!Shell) {
    return <FullScreenLoader />;
  }
  // `createElement` rather than JSX: each shell's component identity is cached once per
  // UI version by `appShells.ts`, so it is stable across renders (only an actual shell
  // switch changes it), which is what the static-components lint rule guards against.
  return React.createElement(Shell, { currentUser, children });
};

/** Subscribes the signed-in tab to backend domain changes (JAN-104); renders nothing. */
const DomainChangeSync: React.FC<{ userId: number }> = ({ userId }) => {
  useDomainChangeSync(userId);
  return null;
};

const AppRouter: React.FC = () => {
  const location = useLocation();
  const { uiVersion, isOverridden, preferredUIVersion } = useUIVersion();
  const { data: setupStatus, isLoading: isSetupLoading } = useQuery(setupStatusQueryOptions);

  const setupRequired = setupStatus?.needs_setup ?? false;
  const isPublicRoute = location.pathname === '/login' || location.pathname === '/setup';
  const shouldCheckAuth = !setupRequired && !isPublicRoute;

  const { data: currentUser, isLoading: isUserLoading, error: userError } = useQuery({
    queryKey: ['me'],
    queryFn: async () => (await authApi.getMe()).data,
    retry: false,
    enabled: shouldCheckAuth,
  });

  // The backend is the source of truth for the shell preference; a mismatch with the
  // localStorage cache silently updates the cache (and the <html> class) instead.
  useSyncUIVersionFromServer(currentUser?.ui_version);

  // Start fetching the cached shell's chunk while `/api/auth/me` is still in flight
  // (JAN-179); the public routes (login, setup) never need a shell.
  useEffect(() => {
    if (shouldCheckAuth) {
      loadAppShell(uiVersion).catch(() => undefined);
    }
  }, [shouldCheckAuth, uiVersion]);

  if (isSetupLoading || (shouldCheckAuth && isUserLoading)) {
    return <FullScreenLoader />;
  }

  if (setupRequired) {
    if (location.pathname !== '/setup') {
      return <Navigate to="/setup" replace />;
    }

    return (
      <Suspense fallback={<FullScreenLoader />}>
        <Setup />
      </Suspense>
    );
  }

  if (location.pathname === '/setup') {
    return <Navigate to="/login" replace />;
  }

  if (location.pathname === '/login') {
    return (
      <Suspense fallback={<FullScreenLoader />}>
        <Login />
      </Suspense>
    );
  }

  if (userError || !currentUser) {
    return <Navigate to="/login" replace />;
  }

  // When the server value differs from the cached value, use the server value
  // immediately for this render to avoid flashing the wrong shell. However, respect
  // query parameter overrides (?ui=...), which always win. The useSyncUIVersionFromServer
  // call above will update the cache for subsequent renders.
  const resolvedUIVersion =
    !isOverridden && currentUser?.ui_version && currentUser.ui_version !== preferredUIVersion
      ? currentUser.ui_version
      : uiVersion;

  // The chat session sits above the shell so switching pages or shells keeps the
  // tab's single /ws/chat socket, transcript and draft (JAN-248). The page-context
  // registry around it lets every message say which page the user is on (JAN-250), and
  // the page-action registry lets Janus act on that page (JAN-251).
  return (
    <PageContextProvider>
      <UIActionProvider>
        <ChatSessionProvider userId={currentUser.id}>
          <DomainChangeSync userId={currentUser.id} />
          <WhatsNewFlash userId={currentUser.id} />
          <AppShellHost version={resolvedUIVersion} currentUser={currentUser}>
            <Suspense fallback={<PageLoader />}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/briefing" element={<MorningBriefing />} />
                <Route path="/memory" element={<MemoryPage />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/planner" element={<PlannerPage />} />
                <Route path="/emails" element={<Emails />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/docs/*" element={<Documentation />} />
                {/* Legacy routes — redirect to the Settings tabs where these pages now live */}
                <Route path="/events" element={<Navigate to="/settings?tab=events" replace />} />
                <Route path="/status" element={<Navigate to="/settings?tab=status" replace />} />
                <Route path="/errors" element={<Navigate to="/settings?tab=errors" replace />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </AppShellHost>
        </ChatSessionProvider>
      </UIActionProvider>
    </PageContextProvider>
  );
};

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <UIVersionProvider>
          <ErrorBoundary title="The application crashed">
            <Routes>
              <Route path="*" element={<AppRouter />} />
            </Routes>
          </ErrorBoundary>
        </UIVersionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
