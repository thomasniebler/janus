import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { janusApi } from './api/janus';
import { flattenTasks, getNextSelectedTaskId } from './taskTree';
import type { ConnectionSettings, PlannerTask, TaskFilter, TaskPatch, Urgency } from './types';

const urgencyOrder: Urgency[] = ['high', 'normal', 'low'];

const urgencyPresentation: Record<Urgency, { label: 'High' | 'Medium' | 'Low'; icon: string; description: string; tone: 'high' | 'medium' | 'low' }> = {
  critical: { label: 'High', icon: '!', description: 'High urgency — needs attention now', tone: 'high' },
  high: { label: 'High', icon: '!', description: 'High urgency — needs attention now', tone: 'high' },
  normal: { label: 'Medium', icon: '▲', description: 'Medium urgency — plan this soon', tone: 'medium' },
  low: { label: 'Low', icon: '≈', description: 'Low urgency — calm, flexible timing', tone: 'low' },
};

function urgencyLabel(urgency: Urgency) {
  return urgencyPresentation[urgency].label;
}

function formatDueDate(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function App() {
  const [settings, setSettings] = useState<ConnectionSettings | null>(null);
  const [tasks, setTasks] = useState<PlannerTask[]>([]);
  const [conversations, setConversations] = useState<{ id: number; title: string | null }[]>([]);
  const [filter, setFilter] = useState<TaskFilter>('all');
  const [showCompleted, setShowCompleted] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [newTitle, setNewTitle] = useState('');
  const [newUrgency, setNewUrgency] = useState<Urgency>('normal');
  const [editTitle, setEditTitle] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editUrgency, setEditUrgency] = useState<Urgency>('normal');
  const [savingTask, setSavingTask] = useState(false);
  const [chatInput, setChatInput] = useState('');
  const [chatReply, setChatReply] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [login, setLogin] = useState({ baseUrl: 'http://localhost:8080', username: '', password: '' });
  const selectedIdRef = useRef<number | null>(null);

  const selectedTask = useMemo(() => flattenTasks(tasks).find((task) => task.id === selectedId) ?? null, [selectedId, tasks]);
  const flatTasks = useMemo(() => flattenTasks(tasks), [tasks]);
  const openCount = flatTasks.filter((task) => task.status !== 'Done').length;
  const completedCount = flatTasks.filter((task) => task.status === 'Done').length;
  const priorityCount = flatTasks.filter((task) => task.status !== 'Done' && (task.urgency === 'critical' || task.urgency === 'high')).length;

  const syncEditor = useCallback((task: PlannerTask | null) => {
    setEditTitle(task?.title ?? '');
    setEditDescription(task?.description ?? '');
    setEditUrgency(task?.urgency ?? 'normal');
  }, []);

  const setSelectedTask = useCallback((task: PlannerTask | null) => {
    const nextSelectedId = task?.id ?? null;
    selectedIdRef.current = nextSelectedId;
    setSelectedId(nextSelectedId);
    syncEditor(task);
  }, [syncEditor]);

  const applyRefreshedTasks = useCallback((nextTasks: PlannerTask[]) => {
    const currentSelectedId = selectedIdRef.current;
    const nextSelectedId = getNextSelectedTaskId(currentSelectedId, nextTasks);
    const nextSelectedTask = flattenTasks(nextTasks).find((task) => task.id === nextSelectedId) ?? null;
    setTasks(nextTasks);
    selectedIdRef.current = nextSelectedId;
    setSelectedId(nextSelectedId);
    if (nextSelectedId !== currentSelectedId) {
      syncEditor(nextSelectedTask);
    }
  }, [syncEditor]);

  const refresh = useCallback(async () => {
    try {
      const nextTasks = await janusApi.listTasks(showCompleted);
      setError(null);
      applyRefreshedTasks(nextTasks);
    } catch (reason) {
      setError(String(reason));
    }
  }, [applyRefreshedTasks, showCompleted]);

  useEffect(() => {
    janusApi.settings().then((nextSettings) => {
      setSettings(nextSettings);
      setLogin((current) => ({ ...current, baseUrl: nextSettings.base_url }));
    }).catch((reason) => setError(String(reason)));
  }, []);

  useEffect(() => {
    if (!settings?.connected) return;
    let cancelled = false;

    void (async () => {
      try {
        const [nextTasks, nextConversations] = await Promise.all([
          janusApi.listTasks(showCompleted),
          janusApi.listConversations(),
        ]);
        if (cancelled) return;
        setError(null);
        applyRefreshedTasks(nextTasks);
        setConversations(nextConversations);
      } catch (reason) {
        if (!cancelled) {
          setError(String(reason));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [applyRefreshedTasks, settings?.connected, showCompleted]);

  useEffect(() => {
    const hideOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') void janusApi.hideSidebar();
    };
    window.addEventListener('keydown', hideOnEscape);
    return () => window.removeEventListener('keydown', hideOnEscape);
  }, []);

  const visibleTasks = useMemo(() => {
    if (filter === 'all') return tasks;
    const matches = new Set(flattenTasks(tasks).filter((task) => {
      const tone = urgencyPresentation[task.urgency].tone;
      return (filter === 'high' && tone === 'high') || (filter === 'normal' && tone === 'medium') || (filter === 'low' && tone === 'low');
    }).map((task) => task.id));
    const keepParents = (items: PlannerTask[]): PlannerTask[] =>
      items.flatMap((task) => {
        const children = task.children ? keepParents(task.children) : [];
        return matches.has(task.id) || children.length ? [{ ...task, children }] : [];
      });
    return keepParents(tasks);
  }, [filter, tasks]);

  async function doLogin(event: FormEvent) {
    event.preventDefault();
    try {
      setError(null);
      setSettings(await janusApi.login(login.baseUrl, login.username, login.password));
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function addTask(parentTaskId: number | null) {
    if (!newTitle.trim()) return;
    try {
      const created = await janusApi.createTask(newTitle.trim(), parentTaskId, newUrgency);
      setNewTitle('');
      setSelectedTask(created);
      await refresh();
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function complete(taskId: number) {
    try {
      await janusApi.completeTask(taskId);
      await refresh();
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function saveTask(event: FormEvent) {
    event.preventDefault();
    if (!selectedTask || !editTitle.trim()) return;
    const patch: TaskPatch = { title: editTitle.trim(), description: editDescription.trim() || null, urgency: editUrgency };
    try {
      setSavingTask(true);
      setError(null);
      await janusApi.updateTask(selectedTask.id, patch);
      await refresh();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSavingTask(false);
    }
  }

  async function sendChat(event: FormEvent) {
    event.preventDefault();
    if (!chatInput.trim()) return;
    const prompt = chatInput.trim();
    setChatInput('');
    setChatReply('Thinking…');
    try {
      await janusApi.sendChat(prompt, 'todo tree', (reply) => setChatReply(reply));
      await refresh();
    } catch (reason) {
      const message = `Chat unavailable: ${String(reason)}`;
      setChatReply(message);
      setError(message);
    }
  }

  if (!settings?.connected) {
    return (
      <main className="login-shell">
        <section className="card login-card">
          <p className="eyebrow">JANUS COMPANION</p>
          <h1>Todo Chat</h1>
          <p className="muted">A focused task list and Janus to help you move it forward.</p>
          <div className="login-benefits"><span><b>01</b> Tasks stay in Janus</span><span><b>02</b> Chat speaks as your Janus character</span><span><b>03</b> Credentials stay in the desktop keyring</span></div>
          <form onSubmit={doLogin} className="stack">
            <label>Janus URL<input value={login.baseUrl} onChange={(event) => setLogin({ ...login, baseUrl: event.target.value })} /></label>
            <label>Username<input autoComplete="username" value={login.username} onChange={(event) => setLogin({ ...login, username: event.target.value })} /></label>
            <label>Password<input type="password" autoComplete="current-password" value={login.password} onChange={(event) => setLogin({ ...login, password: event.target.value })} /></label>
            <button className="primary">Connect</button>
          </form>
          {error && <p className="error">{error}</p>}
          <p className="setup-note">For browser preview, run <code>npm --prefix modules/todo-chat/frontend run dev</code>. For the real sidebar, follow the module README.</p>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div><p className="eyebrow">JANUS COMPANION</p><h1>Todo Chat</h1></div>
        <div className="top-actions"><span className="connection-dot">● Connected</span><button onClick={() => void janusApi.hideSidebar()} aria-label="Hide sidebar">Hide</button></div>
      </header>
      <div className="workspace">
        <section className="tasks-pane">
          <div className="pane-heading"><div><h2>Tasks</h2><span className="muted">{openCount} open · {priorityCount} priority</span></div><button onClick={() => void refresh()}>Refresh</button></div>
          <div className="stat-strip"><div><strong>{openCount}</strong><span>Open</span></div><div><strong>{priorityCount}</strong><span>Priority</span></div><div><strong>{completedCount}</strong><span>Done</span></div></div>
          <div className="filters">
            <select value={filter} onChange={(event) => setFilter(event.target.value as TaskFilter)}><option value="all">All urgency</option>{urgencyOrder.slice().reverse().map((value) => <option key={value} value={value}>{urgencyLabel(value)}</option>)}</select>
            <label className="check"><input type="checkbox" checked={showCompleted} onChange={(event) => setShowCompleted(event.target.checked)} /> Completed</label>
          </div>
          <div className="task-tree">{visibleTasks.length ? visibleTasks.map((task) => <TaskNode key={task.id} task={task} depth={0} selectedId={selectedId} onSelect={setSelectedTask} onComplete={complete} />) : <EmptyTasks onPrompt={(prompt) => setChatInput(prompt)} />}</div>
          <form onSubmit={(event) => { event.preventDefault(); void addTask(null); }} className="add-task"><input aria-label="Task title" placeholder="Add a task…" value={newTitle} onChange={(event) => setNewTitle(event.target.value)} /><select aria-label="New task urgency" value={newUrgency} onChange={(event) => setNewUrgency(event.target.value as Urgency)}>{urgencyOrder.slice().reverse().map((value) => <option key={value} value={value}>{urgencyLabel(value)}</option>)}</select><button className="primary">Add task</button><button type="button" className="secondary" disabled={!selectedTask} onClick={() => void addTask(selectedTask?.id ?? null)}>Add subtask</button></form>
        </section>
        <section className="details-pane">
          <div className="pane-heading"><div><h2>Details</h2><span className="muted">{selectedTask ? 'Edit the selected task' : 'Select a task to begin'}</span></div></div>
          {selectedTask ? <form className="details-form" onSubmit={saveTask}>
            <label>Task title<input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></label>
            <label>Notes<textarea value={editDescription} onChange={(event) => setEditDescription(event.target.value)} placeholder="Add context, links, or the next step…" rows={5} /></label>
            <label>Urgency<select value={editUrgency === 'critical' ? 'high' : editUrgency} onChange={(event) => setEditUrgency(event.target.value as Urgency)}>{urgencyOrder.slice().reverse().map((value) => <option key={value} value={value}>{urgencyLabel(value)}</option>)}</select></label>
            <div className="detail-meta"><span>{selectedTask.status}</span>{selectedTask.parent_task_id && <span>Subtask</span>}{selectedTask.due_at && <span>Due {formatDueDate(selectedTask.due_at)}</span>}</div>
            <div className="detail-actions"><button className="primary" disabled={savingTask}>{savingTask ? 'Saving…' : 'Save changes'}</button>{selectedTask.status !== 'Done' && <button type="button" className="secondary" onClick={() => void complete(selectedTask.id)}>Mark done</button>}</div>
          </form> : <div className="details-empty"><span className="detail-icon">✦</span><strong>Your task details live here</strong><p>Choose a task from the list to edit its notes, urgency, and status.</p></div>}
        </section>
        <section className="chat-pane">
          <div className="pane-heading"><div><h2>Chat</h2><span className="muted">{settings.username}</span></div><div className="chat-controls"><select aria-label="Conversation" value={settings.conversation_id ?? ''} onChange={(event) => janusApi.setConversationId(event.target.value ? Number(event.target.value) : null).then(setSettings).catch((reason) => setError(String(reason)))}><option value="">New conversation</option>{conversations.map((conversation) => <option key={conversation.id} value={conversation.id}>{conversation.title || `Conversation ${conversation.id}`}</option>)}</select></div></div>
          <div className="chat-content"><div className="assistant-bubble"><span className="bubble-kicker">JANUS</span>Ask me to break down, prioritize, or update your tasks. All changes are made through the authorized local-task tools.</div>{!chatReply && <div className="quick-prompts"><button onClick={() => setChatInput('What should I focus on today?')}>Focus for today</button><button onClick={() => setChatInput('Break my highest priority task into steps.')}>Break down a task</button><button onClick={() => setChatInput('Clean up my task list and suggest priorities.')}>Tidy my list</button></div>}{chatReply && <div className="assistant-bubble response">{chatReply}</div>}</div>
          <form onSubmit={sendChat} className="chat-form"><textarea value={chatInput} onChange={(event) => setChatInput(event.target.value)} placeholder="What should we plan?" rows={3} /><button className="primary">Send</button></form>
        </section>
      </div>
      {error && <button className="error-toast" onClick={() => setError(null)}>{error}</button>}
    </main>
  );
}

function EmptyTasks({ onPrompt }: { onPrompt: (prompt: string) => void }) {
  return <div className="empty-state"><span className="empty-icon">✓</span><strong>Your task list is ready</strong><p>Add a task below, or ask Janus to turn an idea into a plan.</p><button className="secondary" onClick={() => onPrompt('Help me plan my next task.')}>Ask Janus for a plan</button></div>;
}

function TaskNode({ task, depth, selectedId, onSelect, onComplete }: { task: PlannerTask; depth: number; selectedId: number | null; onSelect: (task: PlannerTask) => void; onComplete: (id: number) => void }) {
  const urgency = urgencyPresentation[task.urgency];
  return <div><div className={`task-row ${selectedId === task.id ? 'selected' : ''} ${task.status === 'Done' ? 'done' : ''}`} style={{ paddingLeft: `${12 + depth * 18}px` }}><button className="task-title" onClick={() => onSelect(task)}><span className={`urgency urgency-${urgency.tone}`} title={urgency.description} aria-label={urgency.description}>{urgency.icon}</span><span className="task-copy"><span>{task.title}</span><small>{task.status}{task.due_at ? ` · Due ${formatDueDate(task.due_at)}` : ''}</small></span></button>{task.open_child_count > 0 && <span className="child-count">{task.open_child_count}</span>}{task.status !== 'Done' && <button className="complete" onClick={() => onComplete(task.id)} aria-label={`Complete ${task.title}`}>✓</button>}</div>{task.children?.map((child) => <TaskNode key={child.id} task={child} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} onComplete={onComplete} />)}</div>;
}

export default App;
