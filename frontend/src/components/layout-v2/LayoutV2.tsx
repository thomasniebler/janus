import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { authApi } from '../../api';
import { BriefingChat } from '../features/BriefingChat';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { isChatRoute, useNavItems } from '../layout/useNavItems';
import { DESKTOP_CHAT_MEDIA_QUERY, useMediaQuery } from '../../hooks/useMediaQuery';
import { AppTopNav } from './AppTopNav';
import type { User } from '../../types';

interface LayoutV2Props {
  children: React.ReactNode;
  currentUser: User;
}

const CHAT_DRAWER_OFFSET = 'lg:pr-[24rem] xl:pr-[27rem]';

/**
 * UIv2 app shell: a "terminal" HUD — near-black CRT ground with a faint lime grid
 * and scanline wash, a monospaced command bar on top, and an optional right-hand
 * chat drawer. It renders exactly the same pages and feature components as the
 * classic shell — only the chrome differs.
 */
const LayoutV2: React.FC<LayoutV2Props> = ({ children, currentUser }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const navItems = useNavItems();
  const [isChatOpen, setIsChatOpen] = useState(true);
  // Exactly one chat view per shell (JAN-248): the drawer at `lg`+ (when open),
  // inline below the page content on narrower screens, and none on /chat itself.
  const isDesktop = useMediaQuery(DESKTOP_CHAT_MEDIA_QUERY);
  const onChatPage = isChatRoute(location.pathname);
  const showDrawerChat = isDesktop && isChatOpen && !onChatPage;
  const showInlineChat = !isDesktop && !onChatPage;

  const handleLogout = async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignore logout errors
    }
    navigate('/login');
  };

  return (
    <div className="relative flex min-h-screen flex-col bg-v2-bg font-sans text-v2-text">
      {/* Decorative CRT backdrop: grid, bloom and scanlines. */}
      <div aria-hidden="true" className="uiv2-layer uiv2-grid" />
      <div aria-hidden="true" className="uiv2-bloom" />
      <div aria-hidden="true" className="uiv2-layer uiv2-scanlines" />

      <div className="relative z-10 flex min-h-screen flex-col">
        <AppTopNav
          items={navItems}
          pathname={location.pathname}
          currentUser={currentUser}
          isChatOpen={isChatOpen}
          onToggleChat={onChatPage ? undefined : () => setIsChatOpen((open) => !open)}
          onLogout={handleLogout}
        />

        {showDrawerChat && (
          <aside
            aria-label="Chat with Janus" data-janus-chat=""
            className="fixed right-0 top-[5.25rem] bottom-0 z-40 flex w-[23rem] flex-col border-l border-v2-border bg-[rgba(5,9,12,0.92)] backdrop-blur-[2px] xl:w-[26rem]"
          >
            <div className="flex items-center gap-2 border-b border-v2-border bg-black/40 px-4 py-2 uiv2-meta text-[10px] text-v2-text-muted">
              <span aria-hidden="true" className="uiv2-beacon h-1.5 w-1.5 rounded-full bg-v2-accent" />
              Channel · Janus
            </div>
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-4">
              <ErrorBoundary title="The chat panel crashed">
                <BriefingChat variant="compactSidebar" />
              </ErrorBoundary>
            </div>
          </aside>
        )}

        <main
          className={`w-full flex-grow px-4 py-8 sm:px-6 lg:px-8 ${showDrawerChat ? CHAT_DRAWER_OFFSET : ''}`}
        >
          <div className="mx-auto min-w-0 max-w-[1280px]">
            {children}

            {showInlineChat && (
              <section className="mt-8" aria-label="Chat with Janus" data-janus-chat="">
                <ErrorBoundary title="The chat panel crashed">
                  <BriefingChat />
                </ErrorBoundary>
              </section>
            )}
          </div>
        </main>

        <footer
          className={`mt-auto border-t border-v2-border bg-black/50 py-4 ${showDrawerChat ? CHAT_DRAWER_OFFSET : ''}`}
        >
          <div className="mx-auto flex w-full max-w-[1280px] flex-wrap items-center justify-center gap-x-3 gap-y-1 px-4 uiv2-meta text-[10px] text-v2-text-muted sm:px-6 lg:px-8">
            <span aria-hidden="true" className="text-v2-accent">
              &gt;_
            </span>
            <span>&copy; 2026 Janus AI · Local · Private · Passive</span>
            <span aria-hidden="true" className="text-v2-border-bright">
              //
            </span>
            <a
              href="https://thomasniebler.github.io/janus/"
              target="_blank"
              rel="noopener noreferrer"
              className="border-b border-v2-accent/40 text-v2-accent hover:border-v2-accent"
            >
              Documentation
            </a>
          </div>
        </footer>
      </div>
    </div>
  );
};

export default LayoutV2;
