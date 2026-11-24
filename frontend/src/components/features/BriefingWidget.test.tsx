import { render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BriefingWidget } from './BriefingWidget';

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  janusIntentionalClose?: boolean;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.onclose?.();
  }

  emit(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

describe('BriefingWidget', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders streamed markdown with topic-aware briefing context', async () => {
    render(<BriefingWidget />);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    act(() => {
      FakeWebSocket.instances[0].emit('__START__');
      FakeWebSocket.instances[0].emit('## Topic Threads\n- **Hiring pipeline** needs attention today.');
      FakeWebSocket.instances[0].emit('__END__');
    });

    expect(screen.getByText('Topic-aware')).toBeInTheDocument();
    expect(screen.getByText(/tracks durable threads alongside today/i)).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Topic Threads' })).toBeInTheDocument();
    expect(screen.getByText('Hiring pipeline')).toBeInTheDocument();
  });

  // The icon-only refresh button had no accessible name (found by the JAN-220 e2e spec).
  it('names the refresh button and reruns the briefing with refresh=1', async () => {
    render(<BriefingWidget />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    act(() => {
      FakeWebSocket.instances[0].emit('__START__');
      FakeWebSocket.instances[0].emit('Today is calm.');
      FakeWebSocket.instances[0].emit('__END__');
    });

    act(() => screen.getByRole('button', { name: 'Refresh briefing' }).click());

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(new URL(FakeWebSocket.instances[1].url).searchParams.get('refresh')).toBe('1');
  });
});
