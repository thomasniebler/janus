import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { authApi } from '../../api';
import { BriefingChat } from '../features/BriefingChat';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { isChatRoute, useNavItems } from '../layout/useNavItems';
import { DESKTOP_CHAT_MEDIA_QUERY, useMediaQuery } from '../../hooks/useMediaQuery';
import { AppTopNavV3 } from './AppTopNavV3';
import type { User } from '../../types';

interface LayoutV3Props {
  children: React.ReactNode;
  currentUser: User;
}

const CHAT_DRAWER_OFFSET = 'lg:pr-[24rem] xl:pr-[27rem]';

/**
 * UIv3 app shell: a "rusty console" — a warm almost-black-red ground stained
 * with corrosion, dusted with film grain and burned in at the edges, with
 * rust-ruled panels and typewriter chrome. It renders exactly the same pages
 * and feature components as the classic and terminal shells; only the chrome
 * differs.
 */
const LayoutV3: React.FC<LayoutV3Props> = ({ children, currentUser }) => {
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
    <div className="relative flex min-h-screen flex-col bg-v3-bg font-typewriter text-v3-text">
      {/* Decorative decay: corrosion stains, grain, a burned-in vignette and a drifting roll band. */}
      <div aria-hidden="true" className="uiv3-layer uiv3-corrosion" />
      <div aria-hidden="true" className="uiv3-layer uiv3-grain" />
      {/* `uiv3-roll` owns its own fixed box (a short band, not a full-screen layer). */}
      <div aria-hidden="true" className="uiv3-roll" />
      <div aria-hidden="true" className="uiv3-layer uiv3-vignette" />

      <div className="relative z-10 flex min-h-screen flex-col">
        <AppTopNavV3
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
            className="fixed right-0 top-[5.9rem] bottom-0 z-40 flex w-[23rem] flex-col border-l-2 border-v3-border bg-[rgba(11,5,4,0.94)] xl:w-[26rem]"
          >
            <div className="flex items-center gap-2 border-b border-v3-border bg-black/40 px-4 py-2 uiv3-stamp text-[9px] text-v3-text-muted">
              <span aria-hidden="true" className="uiv3-ember h-1.5 w-1.5 rounded-full bg-v3-accent" />
              Line · Janus
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
          className={`uiv3-plate mt-auto border-t-2 border-v3-border py-4 ${showDrawerChat ? CHAT_DRAWER_OFFSET : ''}`}
        >
          <div className="mx-auto flex w-full max-w-[1280px] flex-wrap items-center justify-center gap-x-3 gap-y-1 px-4 uiv3-stamp text-[9px] text-v3-text-muted sm:px-6 lg:px-8">
            <span aria-hidden="true" className="text-v3-accent-bright">
              &gt;
            </span>
            <span>&copy; 2026 Janus AI · Local · Private · Passive</span>
            <span aria-hidden="true" className="text-v3-border-bright">
              ::
            </span>
            <a
              href="https://thomasniebler.github.io/janus/"
              target="_blank"
              rel="noopener noreferrer"
              className="border-b border-v3-accent/50 text-v3-accent-bright hover:border-v3-accent"
            >
              Documentation
            </a>
          </div>
        </footer>
      </div>
    </div>
  );
};

export default LayoutV3;
