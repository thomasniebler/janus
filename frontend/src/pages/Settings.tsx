import React, { useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { Activity, AlertTriangle, BarChart3, Bot, Calendar, Check, AlertCircle, GitBranch, Link, Mail, MessageSquare, Monitor, Palette, Shield, Sparkles, Wrench } from 'lucide-react';
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
import { MailIngestionConfig } from '../components/features/MailIngestionConfig';
import { LLMConfig } from '../components/features/LLMConfig';
import { MCPRegistryManager } from '../components/features/MCPRegistryManager';
import { PasswordChangeCard } from '../components/features/PasswordChangeCard';
import { PageSectionNav } from '../components/layout/PageSectionNav';
import { proposalBadgeLabel } from '../components/layout/useNavItems';
import { useOpenProposalCount } from '../components/features/triggers/triggerProposalCount';
import { SkillManager } from '../components/features/SkillManager';
import { AdditionalModelSettings } from '../components/features/AdditionalModelSettings';
import { ToolIntegrationSettings } from '../components/features/ToolIntegrationSettings';
import { Card } from '../components/ui/Card';
import { useTabbedPageContext } from '../hooks/pageContextSections';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { googleOAuthErrorMessage } from '../lib/googleOAuthErrors';
import Events from './Events';
import StatusPage from './StatusPage';
import StatsPage from './StatsPage';
import ErrorsPage from './ErrorsPage';
import { authApi } from '../api';

type TabId =
  | 'accounts'
  | 'whatsapp'
  | 'security'
  | 'appearance'
  | 'briefing'
  | 'character'
  | 'runtime'
  | 'skills'
  | 'mail-ingestion'
  | 'system'
  | 'connections'
  | 'github'
  | 'linear'
  | 'tools'
  | 'events'
  | 'status'
  | 'stats'
  | 'errors';

const SETTINGS_SECTION_IDS: Record<TabId, { tabId: string; panelId: string }> = {
  accounts: { tabId: 'settings-tab-accounts', panelId: 'settings-panel-accounts' },
  whatsapp: { tabId: 'settings-tab-whatsapp', panelId: 'settings-panel-whatsapp' },
  security: { tabId: 'settings-tab-security', panelId: 'settings-panel-security' },
  appearance: { tabId: 'settings-tab-appearance', panelId: 'settings-panel-appearance' },
  briefing: { tabId: 'settings-tab-briefing', panelId: 'settings-panel-briefing' },
  character: { tabId: 'settings-tab-character', panelId: 'settings-panel-character' },
  runtime: { tabId: 'settings-tab-runtime', panelId: 'settings-panel-runtime' },
  skills: { tabId: 'settings-tab-skills', panelId: 'settings-panel-skills' },
  'mail-ingestion': { tabId: 'settings-tab-mail-ingestion', panelId: 'settings-panel-mail-ingestion' },
  system: { tabId: 'settings-tab-system', panelId: 'settings-panel-system' },
  connections: { tabId: 'settings-tab-connections', panelId: 'settings-panel-connections' },
  github: { tabId: 'settings-tab-github', panelId: 'settings-panel-github' },
  linear: { tabId: 'settings-tab-linear', panelId: 'settings-panel-linear' },
  tools: { tabId: 'settings-tab-tools', panelId: 'settings-panel-tools' },
  events: { tabId: 'settings-tab-events', panelId: 'settings-panel-events' },
  status: { tabId: 'settings-tab-status', panelId: 'settings-panel-status' },
  stats: { tabId: 'settings-tab-stats', panelId: 'settings-panel-stats' },
  errors: { tabId: 'settings-tab-errors', panelId: 'settings-panel-errors' },
};

/** Query parameters the Google, Microsoft and MCP OAuth callbacks append to the Settings redirect. */
const OAUTH_CALLBACK_PARAMS = ['connected', 'error', 'oauth_connected', 'oauth_error'] as const;

const Settings: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const { data: currentUser } = useQuery({
    queryKey: ['me'],
    queryFn: async () => (await authApi.getMe()).data,
    staleTime: Infinity,
  });

  const [activeTab, setActiveTab] = useState<TabId>(() => {
    // `?tab=planner` was this tab's name before Google and Microsoft moved here.
    const rawTab = searchParams.get('tab');
    const tab = (rawTab === 'planner' ? 'connections' : rawTab) as TabId | null;
    if (searchParams.get('connected') || searchParams.get('error')) {
      return 'connections';
    }
    if (searchParams.get('oauth_connected') || searchParams.get('oauth_error')) {
      return 'tools';
    }
    const valid = Object.keys(SETTINGS_SECTION_IDS) as TabId[];
    if (tab && valid.includes(tab)) {
      const resolved = tab as TabId;
      if (resolved === 'errors' && !(currentUser?.is_admin ?? false)) {
        return 'accounts';
      }
      return resolved;
    }
    return 'accounts';
  });

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

  // Keep the URL in sync with the active tab, and drop the one-shot OAuth callback
  // parameters (read into the toast above) in the same update: two separate updates in
  // one commit raced, and the tab update put the stale `error` back (JAN-318).
  useEffect(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      OAUTH_CALLBACK_PARAMS.forEach((param) => next.delete(param));
      next.set('tab', activeTab);
      return next;
    }, { replace: true });
  }, [activeTab, setSearchParams]);

  const isAdmin = currentUser?.is_admin ?? false;
  const openProposals = useOpenProposalCount();

  const tabs = [
    { id: 'accounts' as const, name: 'Email Accounts', icon: Mail, ...SETTINGS_SECTION_IDS.accounts },
    { id: 'whatsapp' as const, name: 'WhatsApp', icon: MessageSquare, ...SETTINGS_SECTION_IDS.whatsapp },
    { id: 'security' as const, name: 'Security', icon: Shield, ...SETTINGS_SECTION_IDS.security },
    { id: 'appearance' as const, name: 'Appearance', icon: Palette, ...SETTINGS_SECTION_IDS.appearance },
    { id: 'briefing' as const, name: 'Daily Briefing', icon: Sparkles, ...SETTINGS_SECTION_IDS.briefing },
    { id: 'character' as const, name: 'Character', icon: Sparkles, ...SETTINGS_SECTION_IDS.character },
    { id: 'runtime' as const, name: 'Triggers', icon: Activity, badge: openProposals, badgeLabel: proposalBadgeLabel(openProposals), ...SETTINGS_SECTION_IDS.runtime },
    { id: 'skills' as const, name: 'Skills', icon: Wrench, ...SETTINGS_SECTION_IDS.skills },
    { id: 'mail-ingestion' as const, name: 'Mail Ingestion', icon: Mail, ...SETTINGS_SECTION_IDS['mail-ingestion'] },
    { id: 'system' as const, name: 'AI & System', icon: Bot, ...SETTINGS_SECTION_IDS.system },
    { id: 'connections' as const, name: 'Connections', icon: Calendar, ...SETTINGS_SECTION_IDS.connections },
    { id: 'github' as const, name: 'GitHub', icon: GitBranch, ...SETTINGS_SECTION_IDS.github },
    { id: 'linear' as const, name: 'Linear', icon: Link, ...SETTINGS_SECTION_IDS.linear },
    { id: 'tools' as const, name: 'Tools & Connections', icon: Wrench, ...SETTINGS_SECTION_IDS.tools },
  ];

  const monitoringTabs = [
    { id: 'status' as const, name: 'System Status', icon: Monitor, ...SETTINGS_SECTION_IDS.status },
    { id: 'stats' as const, name: 'Stats', icon: BarChart3, ...SETTINGS_SECTION_IDS.stats },
    { id: 'events' as const, name: 'System Events', icon: Activity, ...SETTINGS_SECTION_IDS.events },
    ...(isAdmin ? [{ id: 'errors' as const, name: 'Error Groups', icon: AlertTriangle, ...SETTINGS_SECTION_IDS.errors }] : []),
  ];

  // Which tab Janus sees in chat (JAN-250); the tab's data comes from its own section.
  useTabbedPageContext('Settings', 'Settings', [...tabs, ...monitoringTabs], activeTab, (tab) => setActiveTab(tab as TabId));

  if (searchParams.get('tab') === 'rules') {
    return <Navigate to="/memory?tab=rules" replace />;
  }

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
          sections={[
            { items: tabs },
            { title: 'Monitoring', items: monitoringTabs },
          ]}
          activeId={activeTab}
          onSelect={(tabId) => setActiveTab(tabId as TabId)}
          ariaLabel="Settings sections"
        />

        <div className="min-w-0">
          <section
            id={SETTINGS_SECTION_IDS.accounts.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.accounts.tabId}
            hidden={activeTab !== 'accounts'}
            tabIndex={activeTab === 'accounts' ? 0 : -1}
          >
            {activeTab === 'accounts' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <AccountManager />
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.whatsapp.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.whatsapp.tabId}
            hidden={activeTab !== 'whatsapp'}
            tabIndex={activeTab === 'whatsapp' ? 0 : -1}
          >
            {activeTab === 'whatsapp' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <WhatsAppAccountManager />
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.security.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.security.tabId}
            hidden={activeTab !== 'security'}
            tabIndex={activeTab === 'security' ? 0 : -1}
          >
            {activeTab === 'security' && (
              <div className="animate-in slide-in-from-right-4 duration-300 space-y-6">
                <PasswordChangeCard />
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.appearance.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.appearance.tabId}
            hidden={activeTab !== 'appearance'}
            tabIndex={activeTab === 'appearance' ? 0 : -1}
          >
            {activeTab === 'appearance' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Appearance settings crashed">
                  <AppearanceSettings />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.briefing.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.briefing.tabId}
            hidden={activeTab !== 'briefing'}
            tabIndex={activeTab === 'briefing' ? 0 : -1}
          >
            {activeTab === 'briefing' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Daily briefing settings crashed">
                  <BriefingTab />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.character.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.character.tabId}
            hidden={activeTab !== 'character'}
            tabIndex={activeTab === 'character' ? 0 : -1}
          >
            {activeTab === 'character' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Character constitution crashed">
                  <CharacterProfileManager />
                </ErrorBoundary>
                <div className="mt-8">
                  <ErrorBoundary title="Adaptive character state crashed">
                    <CharacterStateManager />
                  </ErrorBoundary>
                </div>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.runtime.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.runtime.tabId}
            hidden={activeTab !== 'runtime'}
            tabIndex={activeTab === 'runtime' ? 0 : -1}
          >
            {activeTab === 'runtime' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Runtime trigger manager crashed">
                  <RuntimeTriggerManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.skills.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.skills.tabId}
            hidden={activeTab !== 'skills'}
            tabIndex={activeTab === 'skills' ? 0 : -1}
          >
            {activeTab === 'skills' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Skill management crashed">
                  <SkillManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS['mail-ingestion'].panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS['mail-ingestion'].tabId}
            hidden={activeTab !== 'mail-ingestion'}
            tabIndex={activeTab === 'mail-ingestion' ? 0 : -1}
            className="space-y-6"
          >
            {activeTab === 'mail-ingestion' && (
              <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Mail ingestion settings crashed">
                  <MailIngestionConfig isAdmin={isAdmin} />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.system.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.system.tabId}
            hidden={activeTab !== 'system'}
            tabIndex={activeTab === 'system' ? 0 : -1}
            className="space-y-6"
          >
            {activeTab === 'system' && (
              <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                <LLMConfig isAdmin={isAdmin} />
                <AdditionalModelSettings />

                <Card className="bg-red-50 dark:bg-red-900/10 border-red-100 dark:border-red-900/30 p-6">
                  <h3 className="text-red-600 dark:text-red-400 font-bold flex items-center gap-2 mb-2">
                    <Bot size={18} />
                    Privacy & Security
                  </h3>
                  <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                    Janus runs locally on your machine. All email processing and AI inference happens without your data ever leaving your system (unless using OpenAI).
                  </p>
                  <div className="flex gap-4">
                    <button className="text-xs font-bold text-red-600 dark:text-red-400 underline hover:no-underline">Reset All Data</button>
                    <button className="text-xs font-bold text-red-600 dark:text-red-400 underline hover:no-underline">Export Local Database</button>
                  </div>
                </Card>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.connections.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.connections.tabId}
            hidden={activeTab !== 'connections'}
            tabIndex={activeTab === 'connections' ? 0 : -1}
            className="space-y-6"
          >
            {activeTab === 'connections' && (
              <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Connections crashed">
                  <ConnectionsConfig />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.github.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.github.tabId}
            hidden={activeTab !== 'github'}
            tabIndex={activeTab === 'github' ? 0 : -1}
            className="space-y-6"
          >
            {activeTab === 'github' && (
              <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="GitHub configuration crashed">
                  <GitHubConfig isAdmin={isAdmin} />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.linear.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.linear.tabId}
            hidden={activeTab !== 'linear'}
            tabIndex={activeTab === 'linear' ? 0 : -1}
            className="space-y-6"
          >
            {activeTab === 'linear' && (
              <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Linear configuration crashed">
                  <LinearConfig isAdmin={isAdmin} />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.tools.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.tools.tabId}
            hidden={activeTab !== 'tools'}
            tabIndex={activeTab === 'tools' ? 0 : -1}
            className="space-y-6"
          >
            {activeTab === 'tools' && (
              <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                <ToolIntegrationSettings isAdmin={isAdmin} />
                <ErrorBoundary title="Tool registry crashed">
                  <MCPRegistryManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.status.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.status.tabId}
            hidden={activeTab !== 'status'}
            tabIndex={activeTab === 'status' ? 0 : -1}
          >
            {activeTab === 'status' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="System status crashed">
                  <StatusPage />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.stats.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.stats.tabId}
            hidden={activeTab !== 'stats'}
            tabIndex={activeTab === 'stats' ? 0 : -1}
          >
            {activeTab === 'stats' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Stats crashed">
                  <StatsPage />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.events.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.events.tabId}
            hidden={activeTab !== 'events'}
            tabIndex={activeTab === 'events' ? 0 : -1}
          >
            {activeTab === 'events' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="System events crashed">
                  <Events />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={SETTINGS_SECTION_IDS.errors.panelId}
            role="tabpanel"
            aria-labelledby={SETTINGS_SECTION_IDS.errors.tabId}
            hidden={activeTab !== 'errors'}
            tabIndex={activeTab === 'errors' ? 0 : -1}
          >
            {activeTab === 'errors' && isAdmin && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Error groups crashed">
                  <ErrorsPage />
                </ErrorBoundary>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
};

export default Settings;
