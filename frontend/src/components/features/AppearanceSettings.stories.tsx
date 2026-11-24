import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { authApi } from '../../api';
import { UIVersionContext, type UIVersion, type UIVersionContextValue } from '../../contexts/uiVersion';
import { withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { AppearanceSettings } from './AppearanceSettings';

/** A live `UIVersionContext` provider so clicking a shell option actually re-renders the "Active" state. */
const UIVersionHarness: React.FC<{ initial: UIVersion; forced?: boolean }> = ({ initial, forced = false }) => {
  const [uiVersion, setUiVersion] = useState<UIVersion>(initial);
  const value: UIVersionContextValue = {
    uiVersion,
    preferredUIVersion: uiVersion,
    isOverridden: forced,
    setUIVersion: setUiVersion,
  };
  return (
    <UIVersionContext.Provider value={value}>
      <AppearanceSettings />
    </UIVersionContext.Provider>
  );
};

const meta = {
  title: 'Features/AppearanceSettings',
  component: UIVersionHarness,
  args: { initial: 'v1' },
  argTypes: {
    initial: { control: 'inline-radio', options: ['v1', 'v2', 'v3'] },
  },
  decorators: [
    withQueryData(),
    withApiOverride(authApi, 'updateUIPreference', (version: UIVersion) =>
      Promise.resolve({ data: { id: 1, username: 'janus-admin', is_admin: true, google_linked: false, ui_version: version } } as never)
    ),
  ],
} satisfies Meta<typeof UIVersionHarness>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ClassicSelected: Story = {
  args: { initial: 'v1' },
};

export const TerminalSelected: Story = {
  args: { initial: 'v2' },
};

export const RustyConsoleSelected: Story = {
  args: { initial: 'v3' },
};

export const ForcedByQueryParam: Story = {
  args: { initial: 'v2', forced: true },
};

export const SaveFails: Story = {
  args: { initial: 'v1' },
  decorators: [
    withApiOverride(authApi, 'updateUIPreference', () => Promise.reject(new Error('network error'))),
  ],
};
