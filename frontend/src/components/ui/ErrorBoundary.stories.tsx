import type { Meta, StoryObj } from '@storybook/react';
import { ErrorBoundary } from './ErrorBoundary';

const Boom: React.FC = () => {
  throw new Error('shell exploded');
};

const meta = {
  title: 'UI/ErrorBoundary',
  component: ErrorBoundary,
  parameters: {
    // The thrown error is expected and intentional for the Crashed story;
    // React still logs it to the console, which is fine for this preview.
  },
} satisfies Meta<typeof ErrorBoundary>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Healthy: Story = {
  args: {
    title: 'This section crashed',
    children: <p className="text-sm">Nothing went wrong — children render normally.</p>,
  },
};

export const Crashed: Story = {
  args: {
    title: 'The application crashed',
    children: <Boom />,
  },
};

export const CrashedWithDefaultTitle: Story = {
  args: {
    children: <Boom />,
  },
};
