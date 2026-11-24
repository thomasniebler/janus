import type { Meta, StoryObj } from '@storybook/react';
import { Card, CardContent, CardFooter, CardHeader } from './Card';
import { Button } from './Button';

const meta = {
  title: 'UI/Card',
  component: Card,
  args: {
    children: null,
  },
} satisfies Meta<typeof Card>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Simple: Story = {
  render: () => (
    <Card className="max-w-sm">
      <CardContent>
        <p className="text-sm">A bare card with a single content region.</p>
      </CardContent>
    </Card>
  ),
};

export const WithHeaderAndFooter: Story = {
  render: () => (
    <Card className="max-w-sm">
      <CardHeader>
        <h2 className="text-lg font-semibold">Email accounts</h2>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          me@example.com is connected and syncing every 5 minutes.
        </p>
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button variant="outline" size="sm">
          Cancel
        </Button>
        <Button size="sm">Save</Button>
      </CardFooter>
    </Card>
  ),
};

export const HeaderOnly: Story = {
  render: () => (
    <Card className="max-w-sm">
      <CardHeader>
        <h2 className="text-lg font-semibold">System status</h2>
      </CardHeader>
      <CardContent>
        <p className="text-sm">All background workers healthy.</p>
      </CardContent>
    </Card>
  ),
};
