import React from 'react';
import { ExternalLink } from 'lucide-react';
import { Link } from 'react-router-dom';

import { isAllowedHref } from '../../../lib/uiBlocks';

const LINK_CLASS =
  'inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline uiv2:text-v2-accent uiv3:text-v3-accent-bright';

/**
 * A UI-block link (JAN-252): a same-origin path becomes a router link; an `https://` URL
 * opens in a new tab with `rel="noopener noreferrer"` and shows its host, so the label
 * cannot hide where it goes. Anything else is plain text.
 */
export const BlockLink: React.FC<{ label: string; href: string }> = ({ label, href }) => {
  if (!isAllowedHref(href)) return <span>{label}</span>;
  if (href.startsWith('/')) {
    return (
      <Link to={href} className={LINK_CLASS}>
        {label}
      </Link>
    );
  }
  const host = new URL(href).host;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={LINK_CLASS}>
      {label}
      <span className="text-xs text-gray-500 dark:text-gray-400">({host})</span>
      <ExternalLink size={11} aria-hidden="true" />
    </a>
  );
};
