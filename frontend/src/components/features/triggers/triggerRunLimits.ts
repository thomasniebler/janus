/** Janus's defaults when a definition sets no run limit of its own (`agent_task_runner`). */
export const DEFAULT_MAX_TOOL_CALLS = 8;
export const DEFAULT_MAX_RUN_SECONDS = 180;

const describeOne = (value: number | null | undefined, defaultValue: number, unit: string, off: string): string =>
  value === null || value === undefined ? `${defaultValue} ${unit}` : value === 0 ? off : `${value} ${unit}`;

/** One-line summary of a definition's run limits for its card: null = default, 0 = no limit. */
export const describeRunLimits = (definition: { max_tool_calls?: number | null; max_run_seconds?: number | null }): string =>
  `${describeOne(definition.max_tool_calls, DEFAULT_MAX_TOOL_CALLS, 'rounds', 'no round limit')}, ${describeOne(definition.max_run_seconds, DEFAULT_MAX_RUN_SECONDS, 's', 'no time limit')}`;
