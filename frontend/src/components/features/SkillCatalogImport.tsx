import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, RefreshCw, Search } from 'lucide-react';
import { skillApi } from '../../api';
import type { RemoteSkillSummary } from '../../types';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';

/** Searches the GitHub community skill catalog and imports a result into the shared library (admins only). */
export const SkillCatalogImport: React.FC = () => {
  const queryClient = useQueryClient();
  const [catalogQuery, setCatalogQuery] = useState('');
  const [catalogSearchTerm, setCatalogSearchTerm] = useState('');

  const {
    data: catalogResults = [],
    isFetching: isCatalogLoading,
    error: catalogError,
  } = useQuery<RemoteSkillSummary[]>({
    queryKey: ['skills', 'catalog', catalogSearchTerm],
    queryFn: async () => (await skillApi.discoverSkills(catalogSearchTerm)).data,
    enabled: catalogSearchTerm.trim().length > 0,
  });

  const importMutation = useMutation({
    mutationFn: (remote: RemoteSkillSummary) =>
      skillApi.importSkill({
        remote_id: remote.remote_id,
        name: remote.name,
        description: remote.description,
        license: remote.license ?? undefined,
        content: remote.content ?? undefined,
      }),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
      queryClient.invalidateQueries({ queryKey: ['skills', response.data.id] });
    },
  });

  const handleCatalogSearch = (event: React.FormEvent) => {
    event.preventDefault();
    setCatalogSearchTerm(catalogQuery.trim());
  };

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Download size={16} className="text-primary" />
        <h3 className="text-sm font-semibold">Import from GitHub community catalog</h3>
      </div>
      <form onSubmit={handleCatalogSearch} className="flex flex-col md:flex-row gap-2">
        <Input
          value={catalogQuery}
          onChange={(event) => setCatalogQuery(event.target.value)}
          placeholder="Search remote skills (e.g. incident response)"
        />
        <Button type="submit" icon={<Search size={14} />} disabled={!catalogQuery.trim()}>
          Search
        </Button>
      </form>
      {catalogSearchTerm ? (
        isCatalogLoading ? (
          <div className="h-16 flex items-center justify-center">
            <RefreshCw className="animate-spin text-primary" />
          </div>
        ) : catalogResults.length > 0 ? (
          <div className="space-y-2">
            {catalogResults.map((remote) => (
              <div
                key={remote.remote_id}
                className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 flex flex-col md:flex-row md:items-start md:justify-between gap-3"
              >
                <div className="space-y-1">
                  <p className="font-medium">{remote.name}</p>
                  {remote.description ? (
                    <p className="text-sm text-gray-600 dark:text-gray-300">{remote.description}</p>
                  ) : null}
                  <div className="flex flex-wrap gap-2 text-xs text-gray-500 dark:text-gray-400">
                    <span>Slug: {remote.remote_id}</span>
                    {remote.license ? <span>License: {remote.license}</span> : null}
                  </div>
                </div>
                <Button
                  size="sm"
                  onClick={() => importMutation.mutate(remote)}
                  isLoading={importMutation.isPending}
                  disabled={importMutation.isPending}
                >
                  Import
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">No remote skills found for “{catalogSearchTerm}”.</p>
        )
      ) : null}
      {catalogError ? (
        <p className="text-xs text-red-500">
          {(catalogError as Error).message || 'Unable to search the community skill catalog right now.'}
        </p>
      ) : null}
      {importMutation.error ? (
        <p className="text-xs text-red-500">
          {(importMutation.error as Error).message || 'Unable to import the selected skill.'}
        </p>
      ) : null}
    </Card>
  );
};
