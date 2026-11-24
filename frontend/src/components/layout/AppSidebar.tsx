import React from 'react';
import { Link } from 'react-router-dom';
import { NavBadge } from './NavBadge';
import type { LucideIcon } from 'lucide-react';
import type { User } from '../../types';

export interface AppSidebarItem {
  name: string;
  path: string;
  icon: LucideIcon;
  badge?: number;
  badgeLabel?: string;
}

interface AppSidebarProps {
  items: AppSidebarItem[];
  pathname: string;
  currentUser: User;
}

export const AppSidebar: React.FC<AppSidebarProps> = ({ items, pathname, currentUser }) => {
  return (
    <aside
      className="hidden lg:flex fixed left-0 z-40 w-60 flex-col px-3 py-4 border-r"
      style={{
        top: '56px',
        bottom: 0,
        background: 'rgba(5, 15, 45, 0.50)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        borderRightColor: 'rgba(99, 160, 255, 0.14)',
      }}
    >
      <nav className="space-y-0.5">
        {items.map((item) => {
          const isActive = pathname === item.path || (item.path !== '/' && pathname.startsWith(item.path));
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`janus-nav-link${isActive ? ' active' : ''}`}
            >
              <item.icon size={17} style={{ opacity: isActive ? 1 : 0.85, flexShrink: 0 }} />
              {item.name}
              <NavBadge count={item.badge} label={item.badgeLabel ?? ''} />
            </Link>
          );
        })}
      </nav>

      <div
        className="mt-auto rounded-[6px] px-3 py-2.5 border text-sm"
        style={{
          borderColor: 'rgba(99, 160, 255, 0.14)',
          background: 'rgba(59, 130, 246, 0.06)',
        }}
      >
        <p
          className="text-[9.5px] font-bold uppercase tracking-[0.14em]"
          style={{ color: 'rgba(140, 170, 220, 0.5)' }}
        >
          Signed in as
        </p>
        <p className="mt-1.5 font-semibold text-[13px]" style={{ color: '#e8f0ff' }}>
          {currentUser.username}
        </p>
      </div>
    </aside>
  );
};
