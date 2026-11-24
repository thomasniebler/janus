import React from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';

import { authApi, statusApi } from '../../api';
import { BriefingChat } from '../features/BriefingChat';
import { AppSidebar } from './AppSidebar';
import { isChatRoute, isNavItemActive, useNavItems } from './useNavItems';
import { DESKTOP_CHAT_MEDIA_QUERY, useMediaQuery } from '../../hooks/useMediaQuery';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import type { User } from '../../types';

interface LayoutProps {
  children: React.ReactNode;
  currentUser: User;
}

const LEFT_SIDEBAR_OFFSET = 'lg:pl-[17rem]';
const RIGHT_SIDEBAR_OFFSET = 'lg:pr-[25rem] xl:pr-[29rem]';
const Layout: React.FC<LayoutProps> = ({ children, currentUser }) => {
  const location = useLocation();
  const navigate = useNavigate();

  const handleLogout = async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignore logout errors
    }
    navigate('/login');
  };

  const visibleNavItems = useNavItems();
  // Exactly one chat view per shell (JAN-248): docked on the right at `lg`+, inline
  // below the page content on narrower screens, and none on the /chat page itself.
  const isDesktop = useMediaQuery(DESKTOP_CHAT_MEDIA_QUERY);
  const onChatPage = isChatRoute(location.pathname);
  const showSidebarChat = isDesktop && !onChatPage;
  const showInlineChat = !isDesktop && !onChatPage;
  const rightOffset = showSidebarChat ? RIGHT_SIDEBAR_OFFSET : '';

  const { data: health } = useQuery({
    queryKey: ['health-version'],
    queryFn: async () => (await statusApi.getHealth()).data,
    staleTime: Infinity,
    retry: false,
  });

  return (
    <div className="min-h-screen font-sans" style={{ color: '#e8f0ff' }}>
      {/* Fixed wallpaper background */}
      <div
        aria-hidden="true"
        className="fixed inset-0 bg-cover bg-center pointer-events-none"
        style={{
          backgroundImage: "url('/janus_wallpaper_2.webp')",
          filter: 'blur(3px) brightness(0.55)',
          transform: 'scale(1.04)',
          zIndex: 0,
        }}
      />
      {/* Dark blue overlay */}
      <div
        aria-hidden="true"
        className="fixed inset-0 pointer-events-none"
        style={{ background: 'rgba(4, 14, 38, 0.72)', zIndex: 1 }}
      />

      {/* App shell */}
      <div className="relative flex flex-col min-h-screen" style={{ zIndex: 2 }}>
        {/* Header */}
        <header
          className="sticky top-0 z-50 w-full border-b"
          style={{
            height: '56px',
            background: 'rgba(4, 12, 40, 0.60)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            borderBottomColor: 'rgba(99, 160, 255, 0.14)',
          }}
        >
          <div className="w-full h-full px-4 sm:px-5 lg:px-6 flex items-center justify-between">
            <div className="flex items-center gap-4 lg:gap-6">
              <Link to="/" className="flex items-center gap-2.5 hover:opacity-90 transition-opacity">
                <span className="text-[22px] leading-none">🦉</span>
                <span className="leading-tight">
                  <span
                    className="block text-[19px] font-bold tracking-tight"
                    style={{
                      background: 'linear-gradient(135deg, #93c5fd 0%, #60a5fa 50%, #3b82f6 100%)',
                      WebkitBackgroundClip: 'text',
                      WebkitTextFillColor: 'transparent',
                      backgroundClip: 'text',
                    }}
                  >
                    Janus
                  </span>
                  <span
                    className="hidden text-[9.5px] font-semibold uppercase tracking-[0.16em] sm:block"
                    style={{ color: 'rgba(140, 170, 220, 0.5)' }}
                  >
                    Local AI assistant{health?.version ? ` · v${health.version}` : ''}
                  </span>
                </span>
              </Link>

              <nav className="hidden md:flex lg:hidden items-center gap-0.5">
                {visibleNavItems.map((item) => {
                  const isActive = isNavItemActive(item, location.pathname);
                  return (
                    <Link
                      key={item.path}
                      to={item.path}
                      className="px-3 py-2 rounded-[6px] text-[13px] font-medium flex items-center gap-2 transition-all"
                      style={{
                        background: isActive ? 'rgba(59, 130, 246, 0.18)' : undefined,
                        color: isActive ? '#93c5fd' : 'rgba(180, 210, 255, 0.65)',
                      }}
                    >
                      <item.icon size={15} />
                      {item.name}
                    </Link>
                  );
                })}
              </nav>
            </div>

            <div className="flex items-center gap-1">
              <button
                onClick={handleLogout}
                className="janus-icon-btn janus-icon-btn-danger"
                title="Logout"
              >
                <LogOut size={18} />
              </button>
            </div>
          </div>
        </header>

        <AppSidebar items={visibleNavItems} pathname={location.pathname} currentUser={currentUser} />

        {/* Right chat panel */}
        {showSidebarChat && (
          <aside
            aria-label="Chat with Janus" data-janus-chat=""
            className="flex fixed right-0 z-40 flex-col border-l w-[24rem] xl:w-[28rem] p-4"
            style={{
              top: '56px',
              bottom: 0,
              background: 'rgba(5, 15, 45, 0.50)',
              backdropFilter: 'blur(12px)',
              WebkitBackdropFilter: 'blur(12px)',
              borderLeftColor: 'rgba(99, 160, 255, 0.14)',
            }}
          >
            <ErrorBoundary title="The chat panel crashed">
              <BriefingChat variant="compactSidebar" />
            </ErrorBoundary>
          </aside>
        )}

        <main className={`flex-grow min-h-0 w-full px-3 py-6 sm:px-5 lg:px-6 ${LEFT_SIDEBAR_OFFSET} ${rightOffset}`}>
          <div className="min-w-0">
            {children}

            {showInlineChat && (
              <section className="mt-6" aria-label="Chat with Janus" data-janus-chat="">
                <ErrorBoundary title="The chat panel crashed">
                  <BriefingChat />
                </ErrorBoundary>
              </section>
            )}
          </div>
        </main>

        <footer
          className={`mt-auto border-t py-4 ${LEFT_SIDEBAR_OFFSET} ${rightOffset}`}
          style={{
            borderTopColor: 'rgba(99, 160, 255, 0.14)',
            background: 'rgba(4, 12, 40, 0.40)',
          }}
        >
          <div className="w-full px-3 text-center sm:px-5 lg:px-6">
            <p className="text-sm" style={{ color: 'rgba(140, 170, 220, 0.6)' }}>
              &copy; 2026 Janus AI. Local. Private. Passive.
              <a
                href="https://thomasniebler.github.io/janus/"
                target="_blank"
                rel="noopener noreferrer"
                className="ml-2 hover:text-[#93c5fd] transition-colors"
                style={{ color: 'rgba(180, 210, 255, 0.5)' }}
              >
                Documentation
              </a>
            </p>
          </div>
        </footer>
      </div>
    </div>
  );
};

export default Layout;
