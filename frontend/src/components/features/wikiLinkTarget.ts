import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useSearchParams } from 'react-router-dom';
import { wikiApi } from '../../api';
import type { WikiPageSummary } from '../../types';

/** The listed page a `[[name]]` link means: exact title (case-insensitive) or slug, never a fuzzy match. */
const findWikiPage = (pages: WikiPageSummary[], name: string) => {
  const wanted = name.trim().toLowerCase();
  return pages.find((page) => page.title.trim().toLowerCase() === wanted || page.slug === wanted) ?? null;
};

/**
 * Resolves the `?wiki=<title>` parameter that `[[Page Title]]` links set (JAN-331) through the page search.
 */
const useWikiLinkTarget = () => {
  const [searchParams] = useSearchParams();
  const { key: locationKey } = useLocation();
  const name = searchParams.get('wiki')?.trim() || null;
  const { data: matches } = useQuery<WikiPageSummary[]>({
    queryKey: ['wiki-pages', name],
    queryFn: async () => (await wikiApi.listPages({ query: name ?? '' })).data,
    enabled: name !== null,
  });
  const page = name !== null && matches ? findWikiPage(matches, name) : null;
  return {
    locationKey,
    pageId: page?.id ?? null,
    /** The linked name when the search finished without an exact match. */
    missingName: name !== null && matches && !page ? name : null,
  };
};

/**
 * The Wiki tab's search text and hand-picked page. Both last until the next navigation (the location key
 * changes), so following a `[[Page]]` link always opens its target; a link without a matching page leaves
 * its name in the search box.
 */
export const useWikiSelection = () => {
  const link = useWikiLinkTarget();
  const [picked, setPicked] = useState<{ id: number; key: string } | null>(null);
  const [typed, setTyped] = useState<{ value: string; key: string } | null>(null);
  return {
    query: typed?.key === link.locationKey ? typed.value : (link.missingName ?? ''),
    setQuery: (value: string) => setTyped({ value, key: link.locationKey }),
    pickedId: picked?.key === link.locationKey ? picked.id : null,
    pick: (id: number) => setPicked({ id, key: link.locationKey }),
    linkedPageId: link.pageId,
    missingName: link.missingName,
  };
};
