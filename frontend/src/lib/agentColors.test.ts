import { describe, expect, it } from 'vitest';
import { DEFAULT_AGENT_COLOR, resolveAgentColor, withAlpha } from './agentColors';

describe('agentColors', () => {
  it('returns a valid alpha-appended color when both inputs are valid', () => {
    expect(withAlpha('#112233', '0f')).toBe('#1122330F');
  });

  it('falls back to an opaque suffix when alpha input is invalid', () => {
    expect(withAlpha('#112233', 'xyz')).toBe('#112233FF');
    expect(withAlpha('#112233', '')).toBe('#112233FF');
  });

  it('still falls back to the default base color when the color input is invalid', () => {
    expect(resolveAgentColor('bad-color')).toBe(DEFAULT_AGENT_COLOR);
    expect(withAlpha('bad-color', '1a')).toBe(`${DEFAULT_AGENT_COLOR}1A`);
  });
});
