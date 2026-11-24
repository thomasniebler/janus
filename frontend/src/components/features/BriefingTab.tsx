import React from 'react';
import { BriefingSettings } from './BriefingSettings';
import { DeviceAccessSettings } from './DeviceAccessSettings';
import { SecretarySettings } from './SecretarySettings';

/** The Daily Briefing settings tab: the briefing card, the Secretary's waking hours and alerts, and device access for the inbox. */
export const BriefingTab: React.FC = () => (
  <div className="space-y-6">
    <BriefingSettings />
    <SecretarySettings />
    <DeviceAccessSettings />
  </div>
);
