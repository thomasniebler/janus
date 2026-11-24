import { invoke } from '@tauri-apps/api/core';
import type { ConnectionSettings, PlannerTask, TaskPatch, Urgency } from '../types';

const browserPreviewSettings: ConnectionSettings = {
  base_url: 'http://localhost:8080',
  conversation_id: null,
  connected: false,
  username: null,
};

function inDesktopShell() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

function desktopOnly<T>(command: string, args?: Record<string, unknown>) {
  if (!inDesktopShell()) {
    return Promise.reject(new Error(`Browser preview only: launch the desktop sidebar using modules/todo-chat/README.md to use ${command}.`));
  }
  return invoke<T>(command, args);
}

export const janusApi = {
  settings: () => (inDesktopShell() ? invoke<ConnectionSettings>('get_connection_settings') : Promise.resolve(browserPreviewSettings)),
  login: (baseUrl: string, username: string, password: string) =>
    desktopOnly<ConnectionSettings>('login', { baseUrl, username, password }),
  saveSettings: (baseUrl: string) => desktopOnly<ConnectionSettings>('save_connection_settings', { baseUrl }),
  listTasks: (includeCompleted: boolean) =>
    desktopOnly<PlannerTask[]>('list_local_tasks', { includeCompleted }),
  listConversations: () => desktopOnly<{ id: number; title: string | null }[]>('list_conversations'),
  setConversationId: (conversationId: number | null) =>
    desktopOnly<ConnectionSettings>('set_conversation_id', { conversationId }),
  updateTask: (taskId: number, patch: TaskPatch) =>
    desktopOnly<PlannerTask>('update_local_task', { taskId, patch }),
  createTask: (title: string, parentTaskId: number | null, urgency: Urgency) =>
    desktopOnly<PlannerTask>('create_local_task', { title, parentTaskId, urgency }),
  completeTask: (taskId: number) => desktopOnly<PlannerTask>('complete_local_task', { taskId }),
  hideSidebar: () => desktopOnly<void>('hide_sidebar'),
  sendChat: (text: string, pageContext: string, onEvent: (event: string) => void) =>
    desktopOnly<string>('send_chat', { text, pageContext }).then(onEvent),
};
