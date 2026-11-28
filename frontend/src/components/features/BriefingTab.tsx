import React from 'react';
import { BriefingSettings } from './BriefingSettings';
import { SecretarySettings } from './SecretarySettings';

/** The Daily Briefing settings tab: the briefing card, and the Secretary's waking hours and alerts. */
export const BriefingTab: React.FC = () => (
  <div className="space-y-6">
    <BriefingSettings />
    <SecretarySettings />
  </div>
);
