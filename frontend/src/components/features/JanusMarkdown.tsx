import React from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link, useInRouterContext } from 'react-router-dom';
import { linkWikiPages } from '../../lib/wikiLinks';

/** An app path such as `/memory?tab=wiki&wiki=…`; protocol-relative `//host` URLs are external. */
const isAppPath = (href?: string): href is string => !!href && href.startsWith('/') && !/^\/[/\\]/.test(href);

/** App links route through react-router (no full reload); every other link renders as before. */
const MarkdownLink: Components['a'] = ({ href, title, className, children }) => {
  const inRouter = useInRouterContext();
  return inRouter && isAppPath(href) ? (
    <Link to={href} title={title} className={className}>
      {children}
    </Link>
  ) : (
    <a href={href} title={title} className={className}>
      {children}
    </a>
  );
};

const COMPONENTS: Components = { a: MarkdownLink };

/**
 * Markdown that Janus wrote (chat replies, briefings, wiki pages), with `[[Page Title]]` wiki links made
 * followable (JAN-331). Outside a router (isolated stories), app links fall back to plain anchors.
 */
export const JanusMarkdown: React.FC<{ children: string }> = ({ children }) => (
  <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
    {linkWikiPages(children)}
  </ReactMarkdown>
);
