import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ModelChoiceFields } from './ModelChoiceFields';

// Same shared factory as every other file that (transitively) loads the API client:
// with `isolate: false`, an unmocked load here would leave LLMModelSelect bound to the
// real client for later test files in this worker (see src/test/apiMock.ts).
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

describe('ModelChoiceFields', () => {
  it('defaults to the default model and reports a provider choice', async () => {
    const onChange = vi.fn();
    render(<ModelChoiceFields label="Trigger" provider={null} model={null} onChange={onChange} dataDescription="email content" />);
    const user = userEvent.setup();

    const select = screen.getByLabelText('Trigger model');
    expect(select).toHaveValue('');
    expect(screen.queryByTestId('model-choice-trigger-cloud-hint')).not.toBeInTheDocument();

    await user.selectOptions(select, 'anthropic');
    expect(onChange).toHaveBeenCalledWith('anthropic', null);
  });
});
