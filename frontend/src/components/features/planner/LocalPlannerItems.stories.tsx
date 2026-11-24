import type { Meta, StoryObj } from '@storybook/react';
import { plannerApi } from '../../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../../test/storybookQueryClient';
import { PlannerEvents, PlannerTasks } from './LocalPlannerItems';
import { plannerEventsQueryKey, plannerTasksQueryKey } from './plannerQueryKeys';
import type { CalendarEvent } from '../../../types';

const event: CalendarEvent = {
  id: 1,
  kind: 'event',
  source: 'manual',
  source_label: 'Local',
  read_only: false,
  title: 'Dentist',
  start_time: '2026-11-13T09:00:00Z',
  end_time: '2026-11-13T10:00:00Z',
  all_day: false,
};

const meta = {
  title: 'Features/Planner/LocalPlannerItems',
  component: PlannerEvents,
} satisfies Meta<typeof PlannerEvents>;

export default meta;
type Story = StoryObj<typeof meta>;

export const EventsWithOneLocalEvent: Story = {
  decorators: [withQueryData((queryClient) => queryClient.setQueryData(plannerEventsQueryKey, [event]))],
};

export const EventsEmpty: Story = {
  decorators: [withQueryData((queryClient) => queryClient.setQueryData(plannerEventsQueryKey, []))],
};

export const EventsLoading: Story = {
  decorators: [withQueryData(), withApiOverride(plannerApi, 'getEvents', () => neverResolving())],
};

export const TasksEmpty: Story = {
  render: () => <PlannerTasks />,
  decorators: [withQueryData((queryClient) => queryClient.setQueryData(plannerTasksQueryKey, []))],
};

export const TasksLoading: Story = {
  render: () => <PlannerTasks />,
  decorators: [withQueryData(), withApiOverride(plannerApi, 'getTasks', () => neverResolving())],
};
