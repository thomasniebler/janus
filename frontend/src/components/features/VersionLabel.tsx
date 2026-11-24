import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { statusApi } from '../../api';
import { ChangelogLink } from './ChangelogLink';

/** The running Janus version as a link to the changelog; nothing until the version is known. */
export const VersionLabel: React.FC<{ className?: string }> = ({ className }) => {
  const { data } = useQuery({
    queryKey: ['version-info'],
    queryFn: async () => (await statusApi.getVersion()).data,
    staleTime: Infinity,
    retry: false,
  });
  if (!data) return null;
  return (
    <ChangelogLink
      href={data.changelog_url}
      className={className ?? 'text-sm text-gray-500 hover:underline dark:text-gray-400'}
    >
      Janus v{data.version} · Changelog
    </ChangelogLink>
  );
};
