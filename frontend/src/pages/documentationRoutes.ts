const DOC_ROUTE_TO_SOURCE_PATH: Record<string, string> = {
  'tech-spec': 'tech-spec.md',
};

const DOC_SOURCE_TO_ROUTE_PATH: Record<string, string> = {
  'tech-spec.md': 'tech-spec',
};

function normalizePath(path: string): string {
  const normalizedSegments: string[] = [];

  for (const segment of path.split('/')) {
    if (!segment || segment === '.') {
      continue;
    }

    if (segment === '..') {
      normalizedSegments.pop();
      continue;
    }

    normalizedSegments.push(segment);
  }

  return normalizedSegments.join('/');
}

export function resolveDocSourcePath(docRoutePath: string): string {
  return DOC_ROUTE_TO_SOURCE_PATH[docRoutePath] ?? `${docRoutePath}.md`;
}

export function resolveDocRoutePath(sourcePath: string): string {
  const normalizedSourcePath = normalizePath(sourcePath);
  const aliasedRoutePath = DOC_SOURCE_TO_ROUTE_PATH[normalizedSourcePath];

  if (aliasedRoutePath) {
    return aliasedRoutePath;
  }

  const withoutMarkdownSuffix = normalizedSourcePath.replace(/\.md$/, '');

  if (withoutMarkdownSuffix !== 'index' && withoutMarkdownSuffix.endsWith('/index')) {
    return withoutMarkdownSuffix.slice(0, -'/index'.length);
  }

  return withoutMarkdownSuffix;
}

function resolveDocLinkTarget(currentDocPath: string, href: string): string | undefined {
  if (!href || href.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(href)) {
    return undefined;
  }

  const [rawPath, ...anchorParts] = href.split('#');
  const anchor = anchorParts.length > 0 ? `#${anchorParts.join('#')}` : '';

  if (rawPath.startsWith('/docs/')) {
    const routePath = rawPath.slice('/docs/'.length).replace(/^\//, '');
    const route = routePath.replace(/\.md$/, '').replace(/\/index$/, '') || 'index';
    return `/docs/${route}${anchor}`;
  }

  if (rawPath.startsWith('/')) {
    return undefined;
  }

  if (rawPath === 'TECH_SPEC.md' || rawPath.endsWith('/TECH_SPEC.md')) {
    return `/docs/tech-spec${anchor}`;
  }

  let sourcePath: string | undefined;

  if (rawPath.startsWith('docs/docs/')) {
    sourcePath = rawPath.slice('docs/docs/'.length);
  } else {
    const currentSourcePath = resolveDocSourcePath(currentDocPath);

    if (DOC_SOURCE_TO_ROUTE_PATH[currentSourcePath]) {
      return undefined;
    }

    const currentDir = currentSourcePath.includes('/')
      ? currentSourcePath.slice(0, currentSourcePath.lastIndexOf('/') + 1)
      : '';
    sourcePath = normalizePath(`${currentDir}${rawPath}`);
  }

  if (!sourcePath) {
    return undefined;
  }

  if (sourcePath.endsWith('/')) {
    sourcePath = `${sourcePath}index.md`;
  }

  if (!sourcePath.endsWith('.md')) {
    return undefined;
  }

  return `/docs/${resolveDocRoutePath(sourcePath)}${anchor}`;
}

export function normalizeDocHref(currentDocPath: string, href: string | undefined): string | undefined {
  if (!href) {
    return href;
  }

  return resolveDocLinkTarget(currentDocPath, href) ?? href;
}
