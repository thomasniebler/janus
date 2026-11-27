import React from 'react';
import { useSearchParams } from 'react-router-dom';
import { BookOpen, Brain, FileText, Network, Scale, Tags } from 'lucide-react';
import { PageSectionNav } from '../components/layout/PageSectionNav';
import { KnowledgeGraphManager } from '../components/features/KnowledgeGraphManager';
import { KnowledgeSourcesManager } from '../components/features/KnowledgeSourcesManager';
import { WikiManager } from '../components/features/WikiManager';
import { MemoryManager } from '../components/features/MemoryManager';
import { TopicManager } from '../components/features/TopicManager';
import { RuleManager } from '../components/features/RuleManager';
import { Card } from '../components/ui/Card';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { useTabbedPageContext } from '../hooks/pageContextSections';

type TabId = 'facts' | 'topics' | 'documents' | 'knowledge' | 'wiki' | 'rules';

const MEMORY_SECTION_IDS: Record<TabId, { tabId: string; panelId: string }> = {
  facts: { tabId: 'memory-tab-facts', panelId: 'memory-panel-facts' },
  topics: { tabId: 'memory-tab-topics', panelId: 'memory-panel-topics' },
  documents: { tabId: 'memory-tab-documents', panelId: 'memory-panel-documents' },
  knowledge: { tabId: 'memory-tab-knowledge', panelId: 'memory-panel-knowledge' },
  wiki: { tabId: 'memory-tab-wiki', panelId: 'memory-panel-wiki' },
  rules: { tabId: 'memory-tab-rules', panelId: 'memory-panel-rules' },
};

const MemoryPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab');
  const activeTab: TabId =
    requestedTab === 'rules' ||
    requestedTab === 'topics' ||
    requestedTab === 'documents' ||
    requestedTab === 'knowledge' ||
    requestedTab === 'wiki'
      ? requestedTab
      : 'facts';

  const tabs = [
    { id: 'facts' as const, name: 'Agent Memory', icon: Brain, ...MEMORY_SECTION_IDS.facts },
    { id: 'topics' as const, name: 'Topics', icon: Tags, ...MEMORY_SECTION_IDS.topics },
    { id: 'documents' as const, name: 'Documents', icon: FileText, ...MEMORY_SECTION_IDS.documents },
    { id: 'knowledge' as const, name: 'Knowledge', icon: Network, ...MEMORY_SECTION_IDS.knowledge },
    { id: 'wiki' as const, name: 'Wiki', icon: BookOpen, ...MEMORY_SECTION_IDS.wiki },
    { id: 'rules' as const, name: 'Mail Prioritization Rules', icon: Scale, ...MEMORY_SECTION_IDS.rules },
  ];

  // Which tab Janus sees in chat (JAN-250); the tab's list comes from its own section.
  useTabbedPageContext('Memory', 'Memory & Knowledge', tabs, activeTab);

  const handleTabChange = (tabId: TabId) => {
    const nextSearchParams = new URLSearchParams(searchParams);

    if (tabId !== 'facts') nextSearchParams.set('tab', tabId);
    else nextSearchParams.delete('tab');

    setSearchParams(nextSearchParams);
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Memory & Knowledge</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Manage durable agent memory and the rules that shape how Janus classifies incoming mail.
          </p>
        </div>
      </div>

      <div className="space-y-6">
        <PageSectionNav
          sections={[{ items: tabs }]}
          activeId={activeTab}
          onSelect={(tabId) => handleTabChange(tabId as TabId)}
          ariaLabel="Memory and knowledge sections"
        />

        <div className="min-w-0">
          <section
            id={MEMORY_SECTION_IDS.facts.panelId}
            role="tabpanel"
            aria-labelledby={MEMORY_SECTION_IDS.facts.tabId}
            hidden={activeTab !== 'facts'}
            tabIndex={activeTab === 'facts' ? 0 : -1}
          >
            {activeTab === 'facts' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Agent memory crashed">
                  <MemoryManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={MEMORY_SECTION_IDS.topics.panelId}
            role="tabpanel"
            aria-labelledby={MEMORY_SECTION_IDS.topics.tabId}
            hidden={activeTab !== 'topics'}
            tabIndex={activeTab === 'topics' ? 0 : -1}
          >
            {activeTab === 'topics' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Topic manager crashed">
                  <TopicManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={MEMORY_SECTION_IDS.documents.panelId}
            role="tabpanel"
            aria-labelledby={MEMORY_SECTION_IDS.documents.tabId}
            hidden={activeTab !== 'documents'}
            tabIndex={activeTab === 'documents' ? 0 : -1}
          >
            {activeTab === 'documents' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Documents crashed">
                  <KnowledgeSourcesManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={MEMORY_SECTION_IDS.knowledge.panelId}
            role="tabpanel"
            aria-labelledby={MEMORY_SECTION_IDS.knowledge.tabId}
            hidden={activeTab !== 'knowledge'}
            tabIndex={activeTab === 'knowledge' ? 0 : -1}
          >
            {activeTab === 'knowledge' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Knowledge crashed">
                  <KnowledgeGraphManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={MEMORY_SECTION_IDS.wiki.panelId}
            role="tabpanel"
            aria-labelledby={MEMORY_SECTION_IDS.wiki.tabId}
            hidden={activeTab !== 'wiki'}
            tabIndex={activeTab === 'wiki' ? 0 : -1}
          >
            {activeTab === 'wiki' && (
              <div className="animate-in slide-in-from-right-4 duration-300">
                <ErrorBoundary title="Wiki crashed">
                  <WikiManager />
                </ErrorBoundary>
              </div>
            )}
          </section>

          <section
            id={MEMORY_SECTION_IDS.rules.panelId}
            role="tabpanel"
            aria-labelledby={MEMORY_SECTION_IDS.rules.tabId}
            hidden={activeTab !== 'rules'}
            tabIndex={activeTab === 'rules' ? 0 : -1}
            className="space-y-6"
          >
            {activeTab === 'rules' && (
              <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                <Card className="p-5">
                  <p className="text-sm text-gray-600 dark:text-gray-300">
                    These rules are part of Janus knowledge and shape how incoming mail is prioritized. You can ask Janus
                    using the chat sidebar to help draft or refine a rule, then manage it here.
                  </p>
                </Card>
                <ErrorBoundary title="Rule management crashed">
                  <RuleManager />
                </ErrorBoundary>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
};

export default MemoryPage;
