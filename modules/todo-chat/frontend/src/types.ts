export type TaskStatus = 'Todo' | 'In Progress' | 'Done';
export type Urgency = 'low' | 'normal' | 'high' | 'critical';

export type PlannerTask = {
  id: number;
  title: string;
  description: string | null;
  status: TaskStatus;
  urgency: Urgency;
  priority: string | null;
  due_at: string | null;
  scheduled_for: string | null;
  completed_at: string | null;
  parent_task_id: number | null;
  sort_order: number | null;
  child_count: number;
  open_child_count: number;
  read_only: boolean;
  source: string;
  children?: PlannerTask[];
};

export type ConnectionSettings = {
  base_url: string;
  conversation_id: number | null;
  connected: boolean;
  username: string | null;
};

export type TaskFilter = 'all' | Urgency;

export type TaskPatch = {
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  urgency?: Urgency;
  due_at?: string | null;
  parent_task_id?: number | null;
  sort_order?: number | null;
};
