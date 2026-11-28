import React, { useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  BarChart3,
  Bot,
  Check,
  Link2,
  Monitor,
  Newspaper,
  Palette,
  Shield,
  Sparkles,
  UserRound,
  Wrench,
  Zap,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { AccountManager } from '../components/features/AccountManager';
import { AppearanceSettings } from '../components/features/AppearanceSettings';
import { VersionLabel } from '../components/features/VersionLabel';
import { WhatsAppAccountManager } from '../components/features/WhatsAppAccountManager';
import { CharacterProfileManager } from '../components/features/CharacterProfileManager';
import { CharacterStateManager } from '../components/features/characterState/CharacterStateManager';
import { RuntimeTriggerManager } from '../components/features/RuntimeTriggerManager';
import { BriefingTab } from '../components/features/BriefingTab';
import { GitHubConfig } from '../components/features/GitHubConfig';
import { LinearConfig } from '../components/features/LinearConfig';
import { ConnectionsConfig } from '../components/features/ConnectionsConfig';
import { TelegramConfig } from '../components/features/TelegramConfig';
import { MailIngestionConfig } from '../components/features/MailIngestionConfig';
import { LLMConfig } from '../components/features/LLMConfig';
import { SurfaceModelConfig } from '../components/features/SurfaceModelConfig';
import { CapabilitySettings } from '../components/features/CapabilitySettings';
import { MCPRegistryManager } from '../components/features/MCPRegistryManager';
import { SelfEvolutionConfig } from '../components/features/SelfEvolutionConfig';
import { DeviceAccessSettings } from '../components/features/DeviceAccessSettings';
import { PasswordChangeCard } from '../components/features/PasswordChangeCard';
import { PageSectionNav, type PageSectionNavItem, type PageSectionNavSection } from '../components/layout/PageSectionNav';
import { proposalBadgeLabel } from '../components/layout/useNavItems';
import { useOpenProposalCount } from '../components/features/triggers/triggerProposalCount';
import { SkillManager } from '../components/features/SkillManager';
import { SubTabs } from '../components/ui/SubTabs';
import { useTabbedPageContext } from '../hooks/pageContextSections';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { googleOAuthErrorMessage } from '../lib/googleOAuthErrors';
import Events from './Events';
import StatusPage from './StatusPage';
import StatsPage from './StatsPage';
import ErrorsPage from './ErrorsPage';
import { authApi } from '../api';
import { DEFAULT_TAB, SETTINGS_SUB_TABS, type SettingsLocation, type TabId, locationFor, resolveSettingsLocation } from './settingsTabs';

const sectionIds = (id: string) => ({ tabId: `settings-tab-${id}`, panelId: `settings-panel-${id}` });

/** Query parameters the Google, Microsoft and MCP OAuth callbacks append to the Settings redirect. */
const OAUTH_CALLBACK_PARAMS = ['connected', 'error', 'oauth_connected', 'oauth_error'] as const;

/** One page's panel: only the active page mounts its content. */
const Panel: React.FC<{ id: TabId; active: boolean; children: React.ReactNode }> = ({ id, active, children }) => (
  <section
    id={sectionIds(id).panelId}
    role="tabpanel"
    aria-labelledby={sectionIds(id).tabId}
    hidden={!active}
    tabIndex={active ? 0 : -1}
  >
    {active && <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">{children}</div>}
  </section>
);

/** A sub-tab's panel inside a page. */
const SubPanel: React.FC<{ page: TabId; id: string; active: boolean; children: React.ReactNode }> = ({
  page,
  id,
  active,
  children,
}) => (
  <div
    id={`settings-${page}-panel-${id}`}
    role="tabpanel"
    aria-labelledby={`settings-${page}-tab-${id}`}
    hidden={!active}
    className="space-y-6"
  >
    {active && children}
  </div>
);

const Settings: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const { data: currentUser } = useQuery({
    queryKey: ['me'],
    queryFn: async () => (await authApi.getMe()).data,
    staleTime: Infinity,
  });

  const [location, setLocation] = useState<SettingsLocation>(() => {
    if (searchParams.get('connected') || searchParams.get('error')) {
      return locationFor('connections', 'calendar');
    }
    if (searchParams.get('oauth_connected') || searchParams.get('oauth_error')) {
      return locationFor('tools');
    }
    // Current and legacy `?tab=` ids both resolve; see `LEGACY_TABS`.
    const resolved = resolveSettingsLocation(searchParams.get('tab'), searchParams.get('sub'));
    if (resolved && !(resolved.tab === 'errors' && !(currentUser?.is_admin ?? false))) {
      return resolved;
    }
    return locationFor(DEFAULT_TAB);
  });
  const activeTab = location.tab;
  const activeSub = location.sub;
  const setActiveTab = (tab: TabId) => setLocation(locationFor(tab));
  const setActiveSub = (sub: string) => setLocation((current) => locationFor(current.tab, sub));

  const [toast, setToast] = useState<{ status: 'success' | 'error'; message: string } | null>(() => {
    const connected = searchParams.get('connected');
    const error = searchParams.get('error');
    const oauthConnected = searchParams.get('oauth_connected');
    const oauthError = searchParams.get('oauth_error');
    if (connected === 'google') {
      return { status: 'success', message: 'Google account connected successfully!' };
    }
    if (connected === 'microsoft') {
      return { status: 'success', message: 'Microsoft account connected successfully!' };
    }
    if (oauthConnected) {
      return { status: 'success', message: 'MCP server authorized successfully.' };
    }
    if (oauthError) {
      return { status: 'error', message: 'MCP authorization could not be completed. Please try again.' };
    }
    if (error) {
      return { status: 'error', message: googleOAuthErrorMessage(error) };
    }
    return null;
  });

  // Auto-dismiss toast after 6 seconds.
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, [toast]);

  // Keep the URL in sync with the active tab and sub-tab, and drop the one-shot OAuth callback
  // parameters (read into the toast above) in the same update: two separate updates in
  // one commit raced, and the tab update put the stale `error` back (JAN-318).
  useEffect(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      OAUTH_CALLBACK_PARAMS.forEach((param) => next.delete(param));
      next.set('tab', activeTab);
      if (activeSub) next.set('sub', activeSub);
      else next.delete('sub');
      return next;
    }, { replace: true });
  }, [activeTab, activeSub, setSearchParams]);

  const isAdmin = currentUser?.is_admin ?? false;
  const openProposals = useOpenProposalCount();

  const page = (id: TabId, name: string, icon: PageSectionNavItem['icon']): PageSectionNavItem => ({ id, name, icon, ...sectionIds(id) });

  const sections: PageSectionNavSection[] = [
    {
      title: 'Setup',
      items: [page('system', 'AI & Models', Bot), page('connections', 'Connections', Link2), page('tools', 'Tools & Safety', Wrench)],
    },
    { title: 'Assistant', items: [page('character', 'Character', UserRound), page('skills', 'Skills', Sparkles)] },
    {
      title: 'Automation',
      items: [
        { ...page('runtime', 'Triggers', Zap), badge: openProposals, badgeLabel: proposalBadgeLabel(openProposals) },
        page('briefing', 'Daily Briefing', Newspaper),
      ],
    },
    { title: 'Account', items: [page('security', 'Security', Shield), page('appearance', 'Appearance', Palette)] },
    {
      title: 'Monitoring',
      items: [
        page('status', 'System Status', Monitor),
        page('stats', 'Stats', BarChart3),
        page('events', 'System Events', Activity),
        ...(isAdmin ? [page('errors', 'Error Groups', AlertTriangle)] : []),
      ],
    },
  ];

  // Which tab Janus sees in chat (JAN-250); the tab's data comes from its own section.
  useTabbedPageContext('Settings', 'Settings', sections.flatMap((section) => section.items), activeTab, (tab) =>
    setActiveTab(tab as TabId)
  );

  if (searchParams.get('tab') === 'rules') {
    return <Navigate to="/memory?tab=rules" replace />;
  }

  const subTabs = SETTINGS_SUB_TABS[activeTab];

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      {/* Global toast */}
      {toast && (
        <div
          className={`fixed top-4 right-4 z-50 flex items-center gap-3 px-5 py-3 rounded-2xl shadow-lg text-sm font-medium animate-in slide-in-from-top-2 duration-300 ${
            toast.status === 'success'
              ? 'bg-green-50 text-green-700 dark:bg-green-900/90 dark:text-green-300 border border-green-200 dark:border-green-800'
              : 'bg-red-50 text-red-700 dark:bg-red-900/90 dark:text-red-300 border border-red-200 dark:border-red-800'
          }`}
        >
          {toast.status === 'success' ? <Check size={18} /> : <AlertCircle size={18} />}
          {toast.message}
        </div>
      )}

      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Configure your integrations, AI behavior, and system monitoring.</p>
          <VersionLabel className="mt-1 inline-block text-xs text-gray-400 hover:underline dark:text-gray-500" />
        </div>
      </div>

      <div className="space-y-6">
        <PageSectionNav
          sections={sections}
          activeId={activeTab}
          onSelect={(tabId) => setActiveTab(tabId as TabId)}
          ariaLabel="Settings sections"
        />

        <div className="min-w-0 space-y-6">
          {subTabs && activeSub && (
            <SubTabs
              tabs={subTabs}
              activeId={activeSub}
              onSelect={setActiveSub}
              ariaLabel={`${sections.flatMap((section) => section.items).find((item) => item.id === activeTab)?.name} sections`}
              idPrefix={`settings-${activeTab}`}
            />
          )}

          <Panel id="system" active={activeTab === 'system'}>
            <SubPanel page="system" id="models" active={activeSub === 'models'}>
              <LLMConfig isAdmin={isAdmin} />
              <SurfaceModelConfig />
            </SubPanel>
            <SubPanel page="system" id="capabilities" active={activeSub === 'capabilities'}>
              <CapabilitySettings isAdmin={isAdmin} />
            </SubPanel>
          </Panel>

          <Panel id="connections" active={activeTab === 'connections'}>
            <SubPanel page="connections" id="mail" active={activeSub === 'mail'}>
              <AccountManager />
              <ErrorBoundary title="Mail ingestion settings crashed">
                <MailIngestionConfig isAdmin={isAdmin} />
              </ErrorBoundary>
            </SubPanel>
            <SubPanel page="connections" id="calendar" active={activeSub === 'calendar'}>
              <ErrorBoundary title="Connections crashed">
                <ConnectionsConfig />
              </ErrorBoundary>
            </SubPanel>
            <SubPanel page="connections" id="messaging" active={activeSub === 'messaging'}>
              <WhatsAppAccountManager />
              <TelegramConfig />
            </SubPanel>
            <SubPanel page="connections" id="developer" active={activeSub === 'developer'}>
              <ErrorBoundary title="GitHub configuration crashed">
                <GitHubConfig isAdmin={isAdmin} />
              </ErrorBoundary>
              <ErrorBoundary title="Linear configuration crashed">
                <LinearConfig isAdmin={isAdmin} />
              </ErrorBoundary>
            </SubPanel>
          </Panel>

          <Panel id="tools" active={activeTab === 'tools'}>
            <ErrorBoundary title="Tool registry crashed">
              <MCPRegistryManager />
            </ErrorBoundary>
            <ErrorBoundary title="Self-evolution configuration crashed">
              <SelfEvolutionConfig isAdmin={isAdmin} />
            </ErrorBoundary>
          </Panel>

          <Panel id="character" active={activeTab === 'character'}>
            <ErrorBoundary title="Character constitution crashed">
              <CharacterProfileManager />
            </ErrorBoundary>
            <ErrorBoundary title="Adaptive character state crashed">
              <CharacterStateManager />
            </ErrorBoundary>
          </Panel>

          <Panel id="skills" active={activeTab === 'skills'}>
            <ErrorBoundary title="Skill management crashed">
              <SkillManager isAdmin={isAdmin} />
            </ErrorBoundary>
          </Panel>

          <Panel id="runtime" active={activeTab === 'runtime'}>
            <ErrorBoundary title="Runtime trigger manager crashed">
              <RuntimeTriggerManager />
            </ErrorBoundary>
          </Panel>

          <Panel id="briefing" active={activeTab === 'briefing'}>
            <ErrorBoundary title="Daily briefing settings crashed">
              <BriefingTab />
            </ErrorBoundary>
          </Panel>

          <Panel id="security" active={activeTab === 'security'}>
            <PasswordChangeCard />
            <DeviceAccessSettings />
          </Panel>

          <Panel id="appearance" active={activeTab === 'appearance'}>
            <ErrorBoundary title="Appearance settings crashed">
              <AppearanceSettings />
            </ErrorBoundary>
          </Panel>

          <Panel id="status" active={activeTab === 'status'}>
            <ErrorBoundary title="System status crashed">
              <StatusPage />
            </ErrorBoundary>
          </Panel>

          <Panel id="stats" active={activeTab === 'stats'}>
            <ErrorBoundary title="Stats crashed">
              <StatsPage />
            </ErrorBoundary>
          </Panel>

          <Panel id="events" active={activeTab === 'events'}>
            <ErrorBoundary title="System events crashed">
              <Events />
            </ErrorBoundary>
          </Panel>

          <Panel id="errors" active={activeTab === 'errors' && isAdmin}>
            <ErrorBoundary title="Error groups crashed">
              <ErrorsPage />
            </ErrorBoundary>
          </Panel>
        </div>
      </div>
    </div>
  );
};

export default Settings;
