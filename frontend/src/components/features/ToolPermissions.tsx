import React, { useState } from 'react';
import { Lock, RotateCcw, Shield } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { mcpApi } from '../../api';
import type { components } from '../../generated/api';
import type { MCPBuiltin as RegistryBuiltin, ToolPolicyChanges } from '../../types';

type ToolPolicyModes = components['schemas']['ToolPolicyModes'];
type ToolPolicyModesChanges = components['schemas']['ToolPolicyModesUpdate'];
/** A registry row with what applies once the blanket modes (JAN-359) are taken into account. */
type MCPBuiltin = RegistryBuiltin & { effective_unattended_allowed?: boolean; effective_chat_asks?: boolean };
import { Button } from '../ui/Button';
import { ConfirmDialog } from '../ui/ConfirmDialog';

const writeWarning = (tool: MCPBuiltin) =>
  `Janus will be able to use "${tool.display_name}" (${tool.description}) with nobody watching. ` +
  'Every run still records which tools it used.';

const errorMessage = (error: unknown): string => {
  if (error && typeof error === 'object' && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'The change could not be saved.';
};

/** A tool that asks before every call in chat has an In chat switch (JAN-306); the rest run without asking. */
const hasChatSwitch = (tool: MCPBuiltin) => (tool.default_chat_mode ?? 'allowed') !== 'allowed';

/** What differs from the defaults, and the PUT body that resets exactly that (a locked unattended switch is never sent). */
const resetFor = (tool: MCPBuiltin): ToolPolicyChanges | null => {
  const unattendedChanged =
    !tool.locked_reason && (tool.unattended_allowed ?? false) !== (tool.default_unattended_allowed ?? false);
  const chatChanged = hasChatSwitch(tool) && tool.chat_mode !== undefined && tool.chat_mode !== tool.default_chat_mode;
  if (!unattendedChanged && !chatChanged) return null;
  return {
    ...(unattendedChanged ? { unattended_allowed: null } : {}),
    ...(chatChanged ? { chat_mode: null } : {}),
  };
};

type ChatSwitchMode = 'ask' | 'always' | 'off';
const toChatSwitchMode = (value: string | undefined): ChatSwitchMode =>
  value === 'off' || value === 'always' ? value : 'ask';

/** "In chat" for one tool: Ask every time / Always allow / Off in chat, or a plain "Allowed" for tools that never ask. */
const ChatSwitch: React.FC<{ tool: MCPBuiltin; busy: boolean; onChange: (mode: ChatSwitchMode) => void }> = ({
  tool,
  busy,
  onChange,
}) => {
  if (!hasChatSwitch(tool)) {
    return (
      <p className="text-xs text-gray-500" aria-label={`In chat: ${tool.display_name}`}>
        In chat: Allowed
      </p>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs text-gray-400" htmlFor={`chat-${tool.id}`}>
        In chat
      </label>
      <select
        id={`chat-${tool.id}`}
        aria-label={`In chat: ${tool.display_name}`}
        className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-sm"
        value={toChatSwitchMode(tool.chat_mode)}
        disabled={busy}
        onChange={(event) => onChange(toChatSwitchMode(event.target.value))}
      >
        <option value="ask">Ask every time</option>
        <option value="always">Always allow</option>
        <option value="off">Off in chat</option>
      </select>
    </div>
  );
};

const ALLOW_ALL_UNATTENDED_WARNING =
  'Every tool runs in triggers, automations, and background tasks with nobody watching, whatever its own switch ' +
  'says: Janus can send mail and WhatsApp messages, write to GitHub, Linear, and Jira, generate (paid) images, and ' +
  'call your remote MCP tools without asking. Every run still records which tools it used.';
const ALLOW_ALL_CHAT_WARNING =
  'No tool asks for your OK in chat any more: Janus sends mail and messages and writes to your integrations as ' +
  'soon as it decides to. Tools set to Off in chat stay off.';

/** The two blanket modes (JAN-359): per-tool switches, or allow everything. */
const PolicyModes: React.FC = () => {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ['tool-policy-modes'],
    queryFn: async () => (await mcpApi.getPolicyModes()).data,
  });
  const mutation = useMutation({
    mutationFn: async (changes: ToolPolicyModesChanges) => (await mcpApi.setPolicyModes(changes)).data,
    onSuccess: (modes: ToolPolicyModes) => {
      queryClient.setQueryData(['tool-policy-modes'], modes);
      return queryClient.invalidateQueries({ queryKey: ['mcp-registry'] });
    },
  });
  const [pending, setPending] = useState<ToolPolicyModesChanges | null>(null);
  if (!data) return null;

  const select = (key: 'unattended_mode' | 'chat_mode', value: string) => {
    const changes = { [key]: value === 'allow_all' ? 'allow_all' : 'per_tool' } as ToolPolicyModesChanges;
    if (value === 'allow_all') setPending(changes);
    else mutation.mutate(changes);
  };

  return (
    <div className="mb-4 space-y-2 rounded-xl border border-white/10 p-4" aria-label="Permission modes">
      <div className="flex flex-wrap items-center gap-2">
        <label className="w-28 text-xs text-gray-400" htmlFor="mode-unattended">
          Unattended
        </label>
        <select
          id="mode-unattended"
          className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-sm"
          value={data.unattended_mode}
          disabled={mutation.isPending}
          onChange={(event) => select('unattended_mode', event.target.value)}
        >
          <option value="per_tool">Per tool (below)</option>
          <option value="allow_all">Allow everything</option>
        </select>
        {data.unattended_mode === 'allow_all' && (
          <span className="text-[10px] uppercase text-amber-400">every tool runs unattended</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="w-28 text-xs text-gray-400" htmlFor="mode-chat">
          In chat
        </label>
        <select
          id="mode-chat"
          className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-sm"
          value={data.chat_mode}
          disabled={mutation.isPending}
          onChange={(event) => select('chat_mode', event.target.value)}
        >
          <option value="per_tool">Per tool (below)</option>
          <option value="allow_all">Allow all without asking</option>
        </select>
        {data.chat_mode === 'allow_all' && (
          <span className="text-[10px] uppercase text-amber-400">no approval cards</span>
        )}
      </div>
      <p className="text-xs text-gray-500">
        <em>Allow everything</em> also opens image generation and your remote MCP tools to unattended runs. Page
        actions, UI blocks, reading your open page, and drafting automations or character changes stay chat only, and
        background tasks still only read.
      </p>
      {mutation.isError && (
        <p className="text-sm text-red-400" role="alert">
          {errorMessage(mutation.error)}
        </p>
      )}
      <ConfirmDialog
        isOpen={pending !== null}
        title={pending?.unattended_mode ? 'Allow everything unattended?' : 'Allow all tools in chat without asking?'}
        message={pending?.unattended_mode ? ALLOW_ALL_UNATTENDED_WARNING : ALLOW_ALL_CHAT_WARNING}
        confirmLabel="Allow"
        tone="warning"
        onConfirm={() => {
          if (pending) mutation.mutate(pending);
          setPending(null);
        }}
        onCancel={() => setPending(null)}
      />
    </div>
  );
};

/**
 * Per built-in tool: may unattended runs use it (JAN-255), and, for a tool that asks before every call,
 * does it ask, run without asking, or stay out of chat (JAN-306, JAN-359)? The blanket modes sit on top.
 */
export const ToolPermissions: React.FC<{ tools: MCPBuiltin[] }> = ({ tools }) => {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({ toolId, changes }: { toolId: string; changes: ToolPolicyChanges }) =>
      mcpApi.setToolPolicy(toolId, changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mcp-registry'] }),
  });

  // A non-read tool waiting for the user to confirm unattended use (JAN-275).
  const [pendingTool, setPendingTool] = useState<MCPBuiltin | null>(null);

  const change = (tool: MCPBuiltin, allowed: boolean) => {
    if (allowed && tool.operation !== 'read') {
      setPendingTool(tool);
      return;
    }
    mutation.mutate({ toolId: tool.id, changes: { unattended_allowed: allowed } });
  };

  const confirmPending = () => {
    if (pendingTool) mutation.mutate({ toolId: pendingTool.id, changes: { unattended_allowed: true } });
    setPendingTool(null);
  };

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Shield size={16} className="text-blue-300" />
        <span className="text-xs font-bold uppercase tracking-wider text-gray-400">Built-in Janus tools</span>
      </div>
      <p className="mb-3 text-xs text-gray-500">
        <strong>Unattended</strong> covers everything Janus does with nobody watching: triggers and automations
        (including the morning briefing, the digest, and mail processing) and background tasks. A tool that is off is
        still shown to those runs, but calling it is refused and the refusal is recorded. On a trigger you create
        yourself, picking a tool allows it for that trigger.
      </p>
      <p className="mb-3 text-xs text-gray-500">
        <strong>In chat</strong>, the tools with the biggest effects ask for your OK before every call (
        <em>Ask every time</em>). <em>Always allow</em> lets one run without asking (the approval card's Always allow
        button sets it too). Set one to <em>Off in chat</em> and Janus does not see or offer it in chat at all. Every
        other tool runs in chat without asking.
      </p>
      <PolicyModes />
      {mutation.isError && (
        <p className="mb-3 text-sm text-red-400" role="alert">
          {errorMessage(mutation.error)}
        </p>
      )}
      <ul className="divide-y divide-white/10 rounded-xl border border-white/10" aria-label="Built-in tool permissions">
        {tools.map((tool) => {
          const allowed = tool.unattended_allowed ?? false;
          const resetChanges = resetFor(tool);
          const busy = mutation.isPending && mutation.variables?.toolId === tool.id;
          return (
            <li key={tool.id} className="flex flex-col gap-3 p-4 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-semibold">{tool.display_name}</h3>
                  <span className="text-[10px] uppercase text-gray-500">
                    {tool.operation} · {tool.risk_level} risk
                  </span>
                </div>
                <p className="mt-1 break-words text-sm text-gray-400">{tool.description}</p>
                {tool.connected === false && (
                  <p className="mt-1 text-xs text-amber-400">
                    Not connected yet ({tool.required_capabilities.join(', ')}); Janus cannot use it anywhere until it
                    is.
                  </p>
                )}
                {tool.effective_unattended_allowed && !allowed && (
                  <p className="mt-1 text-xs text-amber-400">Allowed unattended by “Allow everything”.</p>
                )}
                {hasChatSwitch(tool) && tool.chat_mode === 'ask' && tool.effective_chat_asks === false && (
                  <p className="mt-1 text-xs text-amber-400">Runs in chat without asking (“Allow all”).</p>
                )}
                {!!tool.withheld_unattended_tools?.length && (
                  <p className="mt-1 text-xs text-gray-500">
                    Triggers Janus schedules itself never get: {tool.withheld_unattended_tools.join(', ')}.
                  </p>
                )}
              </div>
              <div className="flex shrink-0 flex-col gap-1 md:items-end">
                <ChatSwitch
                  tool={tool}
                  busy={busy}
                  onChange={(mode) => mutation.mutate({ toolId: tool.id, changes: { chat_mode: mode } })}
                />
                {tool.locked_reason ? (
                  <p className="flex items-center gap-1.5 text-xs text-gray-400" aria-label={`Unattended: ${tool.display_name}`}>
                    <Lock size={13} /> {tool.locked_reason}
                  </p>
                ) : (
                  <div className="flex items-center gap-2">
                    <label className="text-xs text-gray-400" htmlFor={`unattended-${tool.id}`}>
                      Unattended
                    </label>
                    <select
                      id={`unattended-${tool.id}`}
                      aria-label={`Unattended: ${tool.display_name}`}
                      className="rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-sm"
                      value={allowed ? 'allowed' : 'off'}
                      disabled={busy}
                      onChange={(event) => change(tool, event.target.value === 'allowed')}
                    >
                      <option value="allowed">Allowed</option>
                      <option value="off">Off</option>
                    </select>
                  </div>
                )}
                {resetChanges && (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase text-amber-400">changed</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<RotateCcw size={13} />}
                      aria-label={`Reset ${tool.display_name}`}
                      disabled={busy}
                      onClick={() => mutation.mutate({ toolId: tool.id, changes: resetChanges })}
                    >
                      Reset
                    </Button>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <ConfirmDialog
        isOpen={pendingTool !== null}
        title="Allow unattended use?"
        message={pendingTool ? writeWarning(pendingTool) : null}
        confirmLabel="Allow"
        tone="warning"
        onConfirm={confirmPending}
        onCancel={() => setPendingTool(null)}
      />
    </div>
  );
};
