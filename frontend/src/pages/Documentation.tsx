import React from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Link, NavLink, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle, Book, Hash } from 'lucide-react';
import { docsApi } from '../api';
import { Card } from '../components/ui/Card';
import { normalizeDocHref, resolveDocRoutePath, resolveDocSourcePath } from './documentationRoutes';

interface NavItem {
  name: string;
  path?: string;
  children?: NavItem[];
}

/**
 * Converts Zensical/MkDocs-specific syntax that react-markdown cannot render:
 *   !!! tip "Title"          → > **tip: Title**\n> ...
 *   === "Tab label"          → ### Tab label
 */
function normalizeDocsMarkdown(raw: string): string {
  const lines = raw.split('\n');
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Admonition: !!! type "Optional title"
    const admonitionMatch = line.match(/^!!!\s+(\w+)(?:\s+"([^"]*)")?/);
    if (admonitionMatch) {
      const kind = admonitionMatch[1];
      const title = admonitionMatch[2];
      out.push(`> **${title ? `${kind.toUpperCase()}: ${title}` : kind.toUpperCase()}**`);
      i++;
      while (i < lines.length && (lines[i].startsWith('    ') || lines[i] === '')) {
        out.push(lines[i] === '' ? '>' : `> ${lines[i].slice(4)}`);
        i++;
      }
      continue;
    }

    // Tab block: === "Label"
    const tabMatch = line.match(/^===\s+"([^"]*)"/);
    if (tabMatch) {
      out.push(`### ${tabMatch[1]}`);
      i++;
      while (i < lines.length && (lines[i].startsWith('    ') || lines[i] === '')) {
        out.push(lines[i].startsWith('    ') ? lines[i].slice(4) : lines[i]);
        i++;
      }
      continue;
    }

    out.push(line);
    i++;
  }

  return out.join('\n');
}

const DocSidebar: React.FC<{ nav: NavItem[] }> = ({ nav }) => {
  const renderNav = (items: NavItem[], level = 0) => {
    return (
      <ul className={level > 0 ? 'ml-4' : ''}>
        {items.map((item) => (
          <li key={item.name}>
            {item.path ? (
              <NavLink
                to={`/docs/${resolveDocRoutePath(item.path)}`}
                className={({isActive}) => `flex items-center gap-2 py-1.5 text-sm transition-colors rounded-md px-2 ${
                  isActive ? 'bg-primary/10 text-primary font-bold' : 'text-gray-500 hover:text-primary hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                {level > 0 && <Hash size={12} />}
                {item.name}
              </NavLink>
            ) : (
              <div className="py-2">
                <p className="text-xs font-bold uppercase text-gray-400">{item.name}</p>
              </div>
            )}
            {item.children && renderNav(item.children, level + 1)}
          </li>
        ))}
      </ul>
    );
  };

  return <nav className="space-y-2">{renderNav(nav)}</nav>;
};

const isNotFound = (error: unknown): boolean => axios.isAxiosError(error) && error.response?.status === 404;

const Documentation: React.FC = () => {
  const params = useParams();
  const docPath = params['*'] || 'index';

  const { data: nav, isLoading: navLoading } = useQuery({
    queryKey: ['docsNav'],
    queryFn: async () => (await docsApi.getNav()).data
  });

  const {
    data: content,
    isLoading: contentLoading,
    error: contentError,
  } = useQuery({
    queryKey: ['docPage', docPath],
    queryFn: async () => (await docsApi.getPage(resolveDocSourcePath(docPath))).data,
    // A missing page stays missing; only other failures are worth another try.
    retry: (failureCount, error) => !isNotFound(error) && failureCount < 2,
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Book size={28} className="text-primary" />
            Documentation
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Learn how to use and configure Janus.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-8 items-start">
        <aside className="lg:col-span-1 lg:sticky lg:top-24">
          <Card className="p-4">
            {navLoading ? (
              <p>Loading navigation...</p>
            ) : (
              <DocSidebar nav={nav || []} />
            )}
          </Card>
        </aside>

        <main className="lg:col-span-3">
          <Card>
            {contentLoading ? (
              <div className="p-20 flex justify-center">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
              </div>
            ) : contentError ? (
              <div role="alert" className="p-8 space-y-3">
                <h2 className="flex items-center gap-2 text-xl font-semibold">
                  <AlertCircle size={20} className="text-amber-500" />
                  {isNotFound(contentError) ? 'Page not found' : 'Could not load this page'}
                </h2>
                <p className="text-gray-500 dark:text-gray-400">
                  {isNotFound(contentError)
                    ? `There is no documentation page at "${docPath}". The link that led here may be out of date.`
                    : 'The documentation page could not be loaded. Try again in a moment.'}
                </p>
                <Link to="/docs" className="inline-block text-primary font-medium hover:underline">
                  Go to the documentation start page
                </Link>
              </div>
            ) : (
              <article className="p-8 prose prose-slate dark:prose-invert max-w-none prose-headings:font-semibold prose-a:text-primary">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children, ...props }) => (
                      (() => {
                        const normalizedHref = normalizeDocHref(docPath, href);

                        if (normalizedHref?.startsWith('/docs/')) {
                          return (
                            <Link to={normalizedHref} {...props}>
                              {children}
                            </Link>
                          );
                        }

                        return (
                          <a href={normalizedHref} {...props}>
                            {children}
                          </a>
                        );
                      })()
                    )
                  }}
                >
                  {normalizeDocsMarkdown(content || '')}
                </ReactMarkdown>
              </article>
            )}
          </Card>
        </main>
      </div>
    </div>
  );
};

export default Documentation;
