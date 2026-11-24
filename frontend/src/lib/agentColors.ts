export const DEFAULT_AGENT_COLOR = '#6366F1';

const HEX_COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const HEX_ALPHA_PATTERN = /^[0-9A-Fa-f]{2}$/;

export const resolveAgentColor = (color: string | null | undefined): string =>
  !color || !HEX_COLOR_PATTERN.test(color) ? DEFAULT_AGENT_COLOR : color;

const resolveAlphaHex = (alphaHex: string): string =>
  HEX_ALPHA_PATTERN.test(alphaHex) ? alphaHex.toUpperCase() : 'FF';

export const withAlpha = (hexColor: string, alphaHex: string): string =>
  `${resolveAgentColor(hexColor)}${resolveAlphaHex(alphaHex)}`;
