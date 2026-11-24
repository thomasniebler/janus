import React from 'react';
import { Link } from 'react-router-dom';
import { NavBadge } from '../layout/NavBadge';
import { LogOut, PanelRightClose, PanelRightOpen } from 'lucide-react';

import { isNavItemActive, type NavItem } from '../layout/useNavItems';
import type { User } from '../../types';

interface AppTopNavProps {
  items: NavItem[];
  pathname: string;
  currentUser: User;
  isChatOpen: boolean;
  /** Omitted on the /chat page, where there is no drawer to toggle (JAN-248). */
  onToggleChat?: () => void;
  onLogout: () => void;
}

/**
 * UIv2 chrome: a near-black "command bar" with monospaced, uppercase navigation,
 * indexed slots, a phosphor-lime active marker and a status readout strip.
 * Deliberately different from the classic shell's glass header plus left sidebar.
 */
export const AppTopNav: React.FC<AppTopNavProps> = ({
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
    <header className="sticky top-0 z-50 w-full border-b border-v2-border bg-[rgba(3,6,9,0.94)] backdrop-blur-[2px]">
      <div
        aria-hidden="true"
        className="h-[3px] w-full bg-gradient-to-r from-v2-accent via-v2-cyan to-transparent"
      />

      <div className="mx-auto flex h-14 w-full max-w-[1600px] items-center gap-5 px-4 sm:px-6 lg:px-8">
        <Link to="/" className="flex shrink-0 items-baseline gap-1.5" aria-label="Janus home">
          <span className="font-terminal text-[17px] font-bold uppercase tracking-[0.34em] text-v2-text">
            Janus
          </span>
          <span aria-hidden="true" className="uiv2-caret font-terminal text-[15px] leading-none text-v2-accent">
            ▮
          </span>
        </Link>

        <nav aria-label="Primary" className="hidden min-w-0 flex-1 items-center gap-0.5 md:flex">
          {items.map((item, index) => {
            const isActive = isNavItemActive(item, pathname);
            return (
              <Link
                key={item.path}
                to={item.path}
                aria-current={isActive ? 'page' : undefined}
                className={`group relative flex items-center gap-2 px-3 py-2.5 font-terminal text-[11px] font-bold uppercase tracking-[0.16em] transition-colors ${
                  isActive
                    ? 'bg-v2-accent-soft text-v2-accent'
                    : 'text-v2-text-muted hover:bg-white/[0.04] hover:text-v2-text'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`text-[9px] font-normal tabular-nums ${
                    isActive ? 'text-v2-accent/70' : 'text-v2-border-bright'
                  }`}
                >
                  {String(index + 1).padStart(2, '0')}
                </span>
                <item.icon size={14} strokeWidth={2.25} />
                <span className="whitespace-nowrap">{item.name}</span>
                <NavBadge count={item.badge} label={item.badgeLabel ?? ''} />
                {isActive && (
                  <span
                    aria-hidden="true"
                    className="absolute inset-x-0 -bottom-px h-[2px] bg-v2-accent shadow-[0_0_10px_1px_rgba(198,242,74,0.75)]"
                  />
                )}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-2">
          <span className="hidden items-center gap-2 border border-v2-border px-2.5 py-1.5 uiv2-meta text-[10px] text-v2-text-muted lg:inline-flex">
            <span aria-hidden="true" className="uiv2-beacon h-1.5 w-1.5 rounded-full bg-v2-cyan" />
            Local
          </span>

          {onToggleChat && (
            <button
              type="button"
              onClick={onToggleChat}
              aria-pressed={isChatOpen}
              title={isChatOpen ? 'Hide the chat drawer' : 'Show the chat drawer'}
              className={`hidden items-center gap-2 border px-3 py-1.5 uiv2-meta text-[10px] transition-colors lg:inline-flex ${
                isChatOpen
                  ? 'border-v2-accent/60 bg-v2-accent-soft text-v2-accent'
                  : 'border-v2-border text-v2-text-muted hover:border-v2-border-bright hover:text-v2-text'
              }`}
            >
              {isChatOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
              Chat
            </button>
          )}

          <span className="hidden items-baseline gap-0.5 font-terminal text-[11px] text-v2-text-muted sm:inline-flex">
            <span aria-hidden="true" className="text-v2-accent">
              @
            </span>
            <span>{currentUser.username}</span>
          </span>

          <button
            type="button"
            onClick={onLogout}
            title="Logout"
            aria-label="Logout"
            className="inline-flex h-9 w-9 items-center justify-center border border-transparent text-v2-text-muted transition-colors hover:border-[#5c2b2b] hover:bg-[rgba(248,113,113,0.08)] hover:text-[#f87171]"
          >
            <LogOut size={17} />
          </button>
        </div>
      </div>

      <div className="hidden items-center gap-3 border-t border-v2-border bg-black/50 px-4 py-1.5 uiv2-meta text-[10px] text-v2-text-muted sm:flex sm:px-6 lg:px-8">
        <span aria-hidden="true" className="text-v2-accent">
          &gt;_
        </span>
        <span className="truncate text-v2-text/80">janus{routeLabel}</span>
        <span aria-hidden="true" className="text-v2-border-bright">
          //
        </span>
        <span className="truncate">{activeItem?.name ?? 'Overview'}</span>
        <span className="ml-auto hidden shrink-0 text-v2-text-muted/70 lg:inline">
          Local · Private · Passive
        </span>
      </div>

      <nav
        aria-label="Primary (compact)"
        className="flex items-center gap-1 overflow-x-auto border-t border-v2-border px-4 py-2 md:hidden"
      >
        {items.map((item) => {
          const isActive = isNavItemActive(item, pathname);
          return (
            <Link
              key={item.path}
              to={item.path}
              aria-current={isActive ? 'page' : undefined}
              className={`flex shrink-0 items-center gap-1.5 border px-2.5 py-1.5 font-terminal text-[10px] font-bold uppercase tracking-[0.14em] ${
                isActive
                  ? 'border-v2-accent/60 bg-v2-accent-soft text-v2-accent'
                  : 'border-transparent text-v2-text-muted'
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
