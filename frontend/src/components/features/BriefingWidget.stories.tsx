import { useEffect } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { FakeWebSocket, withFakeWebSocket } from '../../test/storybookQueryClient';
import { BriefingWidget } from './BriefingWidget';

/** Emits a scripted sequence of WebSocket events onto the widget's fake socket after mount. */
const Scripted: React.FC<{ variant?: 'compact' | 'full'; events: string[] }> = ({ variant, events }) => {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
      events.forEach((event) => socket?.emit(event));
    }, 10);
    return () => window.clearTimeout(timer);
  }, [events]);
  return <BriefingWidget variant={variant} />;
};

const briefingMarkdown =
  '## Topic Threads\n' +
  "- **Hiring pipeline** needs attention today — panel feedback is in, offer decision pending.\n" +
  "- **Q3 roadmap** review moved to Thursday.\n\n" +
  '## Schedule\n' +
  '- 10:00 Standup\n' +
  '- 14:00 1:1 with Priya';

const meta = {
  title: 'Features/BriefingWidget',
  component: BriefingWidget,
  decorators: [withFakeWebSocket()],
} satisfies Meta<typeof BriefingWidget>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Connecting: Story = {
  name: 'Connecting (no events yet)',
};

export const Delivered: Story = {
  render: () => <Scripted events={['__START__', briefingMarkdown, '__END__']} />,
};

/** The briefing trigger is still running; its text arrives as one message once the run completes. */
export const StillRunning: Story = {
  render: () => <Scripted events={['__START__']} />,
};

export const ConnectionError: Story = {
  render: () => <Scripted events={['__ERROR__:Ollama connection refused while drafting the briefing.']} />,
};

export const FullVariant: Story = {
  render: () => <Scripted variant="full" events={['__START__', briefingMarkdown, '__END__']} />,
};

/** Janus cross-references wiki pages with `[[Page Title]]`; they render as links to the Wiki tab (JAN-331). */
export const WithWikiLinks: Story = {
  render: () => (
    <MemoryRouter>
      <Scripted
        events={[
          '__START__',
          '## Topic Threads\n- [[Acme renewal]] is due on 1 November; [[Dana Whitfield|Dana]] owns the draft.',
          '__END__',
        ]}
      />
    </MemoryRouter>
  ),
};
