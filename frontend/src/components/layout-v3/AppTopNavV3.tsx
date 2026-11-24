import React from 'react';
import { Link } from 'react-router-dom';
import { NavBadge } from '../layout/NavBadge';
import { LogOut, PanelRightClose, PanelRightOpen } from 'lucide-react';

import { isNavItemActive, type NavItem } from '../layout/useNavItems';
import type { User } from '../../types';

interface AppTopNavV3Props {
  items: NavItem[];
  pathname: string;
  currentUser: User;
  isChatOpen: boolean;
  /** Omitted on the /chat page, where there is no drawer to toggle (JAN-248). */
  onToggleChat?: () => void;
  onLogout: () => void;
}

/**
 * UIv3 chrome: a rusted equipment panel. Every nav slot is a boxed, rust-ruled
 * key rather than an underlined tab, the type is a worn typewriter face, and the
 * bar sits under a thick oxidised rule. Deliberately heavier and dirtier than
 * UIv2's crisp phosphor command bar.
 */
export const AppTopNavV3: React.FC<AppTopNavV3Props> = ({
  items,
  pathname,
  currentUser,
  isChatOpen,
  onToggleChat,
  onLogout,
}) => {
  const activeItem = items.find((item) => isNavItemActive(item, pathname));
  const routeLabel = pathname === '/' ? '/dashboard' : pathname;

  return (
    <header className="uiv3-plate sticky top-0 z-50 w-full border-b-2 border-v3-border">
      <div
        aria-hidden="true"
        className="h-[3px] w-full bg-gradient-to-r from-v3-accent-dim via-v3-accent to-v3-ember opacity-80"
      />

      <div className="mx-auto flex h-16 w-full max-w-[1600px] items-center gap-5 px-4 sm:px-6 lg:px-8">
        <Link
          to="/"
          className="flex shrink-0 items-baseline gap-1.5 border border-v3-border bg-black/40 px-2.5 py-1.5"
          aria-label="Janus home"
        >
          <span className="uiv3-stamp text-[15px] text-v3-text">Janus</span>
          <span aria-hidden="true" className="uiv3-strike font-typewriter text-[15px] leading-none text-v3-accent-bright">
            _
          </span>
        </Link>

        <nav
          aria-label="Primary"
          className="hidden min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:flex"
        >
          {items.map((item) => {
            const isActive = isNavItemActive(item, pathname);
            return (
              <Link
                key={item.path}
                to={item.path}
                aria-current={isActive ? 'page' : undefined}
                className={`flex shrink-0 items-center gap-1.5 border px-2.5 py-2 font-typewriter text-[11px] font-bold uppercase tracking-[0.06em] transition-colors ${
                  isActive
                    ? 'border-v3-accent bg-v3-accent-soft text-v3-accent-bright'
                    : 'border-v3-border/70 text-v3-text-muted hover:border-v3-border-bright hover:bg-white/[0.03] hover:text-v3-text'
                }`}
              >
                <item.icon size={14} strokeWidth={2} />
                <span className="whitespace-nowrap">{item.name}</span>
                <NavBadge count={item.badge} label={item.badgeLabel ?? ''} />
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-2">
          <span className="hidden items-center gap-2 border border-v3-border px-2.5 py-1.5 uiv3-stamp text-[9px] text-v3-text-muted lg:inline-flex">
            <span aria-hidden="true" className="uiv3-ember h-1.5 w-1.5 rounded-full bg-v3-ember" />
            Local
          </span>

          {onToggleChat && (
            <button
              type="button"
              onClick={onToggleChat}
              aria-pressed={isChatOpen}
              title={isChatOpen ? 'Hide the chat drawer' : 'Show the chat drawer'}
              className={`hidden items-center gap-2 border px-3 py-1.5 uiv3-stamp text-[9px] transition-colors lg:inline-flex ${
                isChatOpen
                  ? 'border-v3-accent bg-v3-accent-soft text-v3-accent-bright'
                  : 'border-v3-border text-v3-text-muted hover:border-v3-border-bright hover:text-v3-text'
              }`}
            >
              {isChatOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
              Chat
            </button>
          )}

          <span className="hidden items-baseline gap-1 font-typewriter text-[12px] text-v3-text-muted sm:inline-flex">
            <span aria-hidden="true" className="text-v3-accent-bright">
              @
            </span>
            <span>{currentUser.username}</span>
          </span>

          <button
            type="button"
            onClick={onLogout}
            title="Logout"
            aria-label="Logout"
            className="inline-flex h-9 w-9 items-center justify-center border border-transparent text-v3-text-muted transition-colors hover:border-v3-accent hover:bg-v3-accent-soft hover:text-v3-accent-bright"
          >
            <LogOut size={17} />
          </button>
        </div>
      </div>

      <div className="hidden items-center gap-3 border-t border-v3-border bg-black/45 px-4 py-1.5 font-typewriter text-[10px] uppercase tracking-[0.16em] text-v3-text-muted sm:flex sm:px-6 lg:px-8">
        <span aria-hidden="true" className="text-v3-accent-bright">
          &gt;
        </span>
        <span className="truncate text-v3-text/80">janus{routeLabel}</span>
        <span aria-hidden="true" className="text-v3-border-bright">
          ::
        </span>
        <span className="truncate">{activeItem?.name ?? 'Overview'}</span>
        <span className="ml-auto hidden shrink-0 text-v3-text-muted/70 lg:inline">
          Local · Private · Passive
        </span>
      </div>

      <nav
        aria-label="Primary (compact)"
        className="flex items-center gap-1.5 overflow-x-auto border-t border-v3-border px-4 py-2 md:hidden"
      >
        {items.map((item) => {
          const isActive = isNavItemActive(item, pathname);
          return (
            <Link
              key={item.path}
              to={item.path}
              aria-current={isActive ? 'page' : undefined}
              className={`flex shrink-0 items-center gap-1.5 border px-2.5 py-1.5 font-typewriter text-[10px] font-bold uppercase tracking-[0.12em] ${
                isActive
                  ? 'border-v3-accent bg-v3-accent-soft text-v3-accent-bright'
                  : 'border-v3-border/70 text-v3-text-muted'
              }`}
            >
              <item.icon size={13} />
              {item.name}
              <NavBadge count={item.badge} label={item.badgeLabel ?? ''} />
            </Link>
          );
        })}
      </nav>
    </header>
  );
};
