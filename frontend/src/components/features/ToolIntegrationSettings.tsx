import React from 'react';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { SelfEvolutionConfig } from './SelfEvolutionConfig';
import { WebSearchConfig } from './WebSearchConfig';

/** The admin-configured integration cards at the top of Settings -> Tools & Connections. */
export const ToolIntegrationSettings: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => (
  <>
    <ErrorBoundary title="Web search configuration crashed">
      <WebSearchConfig isAdmin={isAdmin} />
    </ErrorBoundary>
    <ErrorBoundary title="Self-evolution configuration crashed">
      <SelfEvolutionConfig isAdmin={isAdmin} />
    </ErrorBoundary>
  </>
);
