import type { Meta, StoryObj } from '@storybook/react';
import { withQueryData } from '../../test/storybookQueryClient';
import { ToolPermissions } from './ToolPermissions';
import { toolRegistrySample } from './triggers/triggerFixtures';

const meta = {
  title: 'Features/ToolPermissions',
  component: ToolPermissions,
  decorators: [
    withQueryData((queryClient) => {
      queryClient.setQueryData(['tool-policy-modes'], { unattended_mode: 'per_tool', chat_mode: 'per_tool' });
    }),
  ],
} satisfies Meta<typeof ToolPermissions>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Registry defaults: confirmation-required tools off, a locked row, a disconnected tool, and withheld tool names. */
export const Defaults: Story = {
  args: { tools: toolRegistrySample.builtins },
};

/** "Send mail" runs in chat without asking (Always allow, JAN-359). */
export const AlwaysAllowInChat: Story = {
  args: {
    tools: toolRegistrySample.builtins.map((tool) =>
      tool.id === 'mail.send' ? { ...tool, chat_mode: 'always' as const } : tool,
    ),
  },
};

/** "Unattended: Allow everything" (JAN-359): rows whose own switch is off say the mode allows them. */
export const AllowEverythingUnattended: Story = {
  decorators: [
    withQueryData((queryClient) => {
      queryClient.setQueryData(['tool-policy-modes'], { unattended_mode: 'allow_all', chat_mode: 'per_tool' });
    }),
  ],
  args: {
    tools: toolRegistrySample.builtins.map((tool) => ({ ...tool, effective_unattended_allowed: !tool.locked_reason })),
  },
};

/** "Send mail" is switched Off in chat (JAN-306): withheld from chat, while its unattended switch keeps its default. */
export const OffInChat: Story = {
  args: {
    tools: toolRegistrySample.builtins.map((tool) =>
      tool.id === 'mail.send' ? { ...tool, chat_mode: 'off' as const } : tool,
    ),
  },
};

/** The user allowed "Send mail" and turned "Memory tools" off, so both show as changed with a Reset button. */
export const Changed: Story = {
  args: {
    tools: toolRegistrySample.builtins.map((tool) => {
      if (tool.id === 'mail.send') return { ...tool, unattended_allowed: true };
      if (tool.id === 'memory') return { ...tool, unattended_allowed: false };
      return tool;
    }),
  },
};
