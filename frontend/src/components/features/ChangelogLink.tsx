import React from 'react';
import { Link } from 'react-router-dom';

interface ChangelogLinkProps {
  /** An in-app path (`/docs/changelog`) or an absolute URL. */
  href: string;
  className?: string;
  children: React.ReactNode;
}

/** A link to the changelog: in-app paths use the router, absolute URLs open in a new tab. */
export const ChangelogLink: React.FC<ChangelogLinkProps> = ({ href, className, children }) =>
  href.startsWith('/') ? (
    <Link to={href} className={className}>
      {children}
    </Link>
  ) : (
    <a href={href} target="_blank" rel="noreferrer" className={className}>
      {children}
    </a>
  );
