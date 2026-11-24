import { vi } from 'vitest';

import { describeShellChatPlacement } from '../../test/shellChatPlacement';
import { describeShellNavBadge } from '../../test/shellNavBadge';
import Layout from './Layout';

vi.mock('../features/BriefingChat', () => ({
  BriefingChat: () => <div>Mock briefing chat</div>,
}));

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

describeShellChatPlacement('Layout (UIv1)', Layout);
describeShellNavBadge('Layout (UIv1)', Layout);
