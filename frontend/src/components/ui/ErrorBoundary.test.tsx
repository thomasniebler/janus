import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ErrorBoundary } from './ErrorBoundary';
import { readStoredUIVersion, UI_VERSION_STORAGE_KEY } from '../../contexts/uiVersion';

const Boom: React.FC = () => {
  throw new Error('shell exploded');
};

describe('ErrorBoundary', () => {
  let assign: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    window.localStorage.clear();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    assign = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, href: 'http://localhost/settings?tab=appearance', assign },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('offers a classic-UI reset that clears the cache and pins ?ui=v1', async () => {
    window.localStorage.setItem(UI_VERSION_STORAGE_KEY, 'v2');
    render(
      <ErrorBoundary title="The application crashed">
        <Boom />
      </ErrorBoundary>
    );
    const user = userEvent.setup();

    expect(screen.getByText('The application crashed')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /reset to classic ui/i }));

    expect(readStoredUIVersion()).toBeNull();
    expect(assign).toHaveBeenCalledWith('http://localhost/settings?tab=appearance&ui=v1');
  });
});
