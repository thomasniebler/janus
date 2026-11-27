import { formatServerDateTime } from '../../lib/serverTime';
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { ArrowRight, Pin, Plus, RefreshCw, Save, Search, Tags, Trash2, X } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { topicApi } from '../../api';
import type { TopicDetail, TopicStatus, TopicSummary, TopicUpdate } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Input } from '../ui/Input';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { useTopicsPageContext } from '../../hooks/pageContextSections';

const statusOptions: Array<{ value: TopicStatus | 'all'; label: string }> = [
  { value: 'active', label: 'Active' },
  { value: 'candidate', label: 'Watching' },
  { value: 'snoozed', label: 'Snoozed' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'ignored', label: 'Ignored' },
  { value: 'all', label: 'All' },
];

const statusLabels: Record<TopicStatus, string> = {
  candidate: 'watching',
  active: 'active',
  snoozed: 'snoozed',
  resolved: 'resolved',
  ignored: 'ignored',
};

const statusBadgeVariant = (status: TopicStatus) => {
  if (status === 'active') {
    return 'success';
  }
  if (status === 'candidate') {
    return 'secondary';
  }
  return 'gray';
};

const parseMarkerKeywords = (value: string) =>
  value
    .split(',')
    .map((keyword) => keyword.trim())
    .filter((keyword) => keyword.length > 0);

const ManualTopicEditor: React.FC<{
  topic: TopicDetail;
  isSaving: boolean;
  onSave: (data: TopicUpdate) => void;
}> = ({ topic, isSaving, onSave }) => {
  const [title, setTitle] = useState(topic.title);
  const [summary, setSummary] = useState(topic.summary ?? '');
  const [markerKeywords, setMarkerKeywords] = useState(topic.marker_keywords.join(', '));

  const canSave =
    title.trim().length > 0 &&
    summary.trim().length > 0 &&
    parseMarkerKeywords(markerKeywords).length > 0;

  return (
    <div className="space-y-4 rounded-2xl border border-gray-100 dark:border-gray-800 p-4">
      <div className="flex items-center gap-2">
        <Save size={16} className="text-primary" />
        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Edit manual topic</h4>
      </div>
      <Input
        label="Title"
        aria-label="Edit manual topic title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Topic title"
      />
      <div className="space-y-1">
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">Description</label>
        <textarea
          aria-label="Edit manual topic description"
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          rows={3}
          placeholder="Explain what belongs to this topic."
          className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
        />
      </div>
      <Input
        label="Marker keywords"
        aria-label="Edit manual topic marker keywords"
        value={markerKeywords}
        onChange={(event) => setMarkerKeywords(event.target.value)}
        placeholder="Comma-separated keywords, senders, or phrases"
      />
      <div className="flex justify-end">
        <Button
          type="button"
          variant="outline"
          onClick={() =>
            onSave({
              title: title.trim(),
              summary: summary.trim(),
              marker_keywords: parseMarkerKeywords(markerKeywords),
            })
          }
          isLoading={isSaving}
          disabled={!canSave}
          icon={<Save size={16} />}
        >
          Save changes
        </Button>
      </div>
    </div>
  );
};

/** Refreshes every view a topic edit changes: the lists, the dashboard, and the open topic. */
const invalidateTopicViews = (queryClient: QueryClient) => {
  for (const queryKey of [['topics'], ['dashboard'], ['topic-detail']]) queryClient.invalidateQueries({ queryKey });
};

export const TopicManager: React.FC = () => {
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirmDialog();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<TopicStatus | 'all'>('active');
  const [createTitle, setCreateTitle] = useState('');
  const [createSummary, setCreateSummary] = useState('');
  const [createMarkerKeywords, setCreateMarkerKeywords] = useState('');
  const selectedTopicId = Number.parseInt(searchParams.get('topic') ?? '', 10);
  const hasSelectedTopic = Number.isInteger(selectedTopicId);

  const topicsQueryKey = useMemo(
    () => ['topics', query, statusFilter] as const,
    [query, statusFilter]
  );

  const { data: topics = [], isLoading } = useQuery<TopicSummary[]>({
    queryKey: topicsQueryKey,
    queryFn: async () =>
      (
        await topicApi.list({
          query,
          status: statusFilter === 'all' ? undefined : statusFilter,
          limit: 100,
        })
      ).data,
  });

  const {
    data: selectedTopic,
    isLoading: isSelectedTopicLoading,
    error: selectedTopicError,
  } = useQuery<TopicDetail>({
    queryKey: ['topic-detail', selectedTopicId],
    queryFn: async () => (await topicApi.get(selectedTopicId)).data,
    enabled: hasSelectedTopic,
    retry: false,
  });

  useTopicsPageContext(topics, selectedTopic, statusFilter, query, { setStatus: setStatusFilter, setSearch: setQuery, select: (id) => setSelectedTopicId(id), form: { title: [createTitle, setCreateTitle], summary: [createSummary, setCreateSummary], marker_keywords: [createMarkerKeywords, setCreateMarkerKeywords] } }); // what Janus sees and may drive in chat (JAN-250, JAN-251)

  const updateTopic = useMutation({
    mutationFn: ({ id, data }: { id: number; data: TopicUpdate }) => topicApi.update(id, data),
    onSuccess: () => invalidateTopicViews(queryClient),
  });

  const createTopic = useMutation({
    mutationFn: () =>
      topicApi.create({
        title: createTitle.trim(),
        summary: createSummary.trim(),
        marker_keywords: parseMarkerKeywords(createMarkerKeywords),
      }),
    onSuccess: (response) => {
      setCreateTitle('');
      setCreateSummary('');
      setCreateMarkerKeywords('');
      queryClient.invalidateQueries({ queryKey: ['topics'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      setSelectedTopicId(response.data.id);
    },
  });

  const updateManualTopic = useMutation({
    mutationFn: ({ id, data }: { id: number; data: TopicUpdate }) => topicApi.update(id, data),
    onSuccess: () => invalidateTopicViews(queryClient),
  });

  const deleteTopic = useMutation({
    mutationFn: (id: number) => topicApi.delete(id),
    onSuccess: (_response, id) => {
      invalidateTopicViews(queryClient);
      if (selectedTopicId === id) {
        setSelectedTopicId(null);
      }
    },
  });
  const confirmDeleteTopic = (topic: { id: number; title: string }) =>
    confirm({
      title: 'Delete topic?',
      message: `Delete topic "${topic.title}"?`,
      confirmLabel: 'Delete',
      tone: 'warning',
      onConfirm: () => deleteTopic.mutate(topic.id),
    });

  const canCreateTopic =
    createTitle.trim().length > 0 &&
    createSummary.trim().length > 0 &&
    parseMarkerKeywords(createMarkerKeywords).length > 0;

  const setSelectedTopicId = (topicId: number | null) => {
    const nextSearchParams = new URLSearchParams(searchParams);
    if (topicId === null) {
      nextSearchParams.delete('topic');
    } else {
      nextSearchParams.set('topic', String(topicId));
      nextSearchParams.set('tab', 'topics');
    }
    setSearchParams(nextSearchParams);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <Tags className="text-primary" />
            Topics
          </h2>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Structured recurring threads Janus is tracking across incoming mail.
          </p>
        </div>
      </div>

      <Card className="p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Plus size={18} className="text-primary" />
          <h3 className="text-lg font-bold">Add manual topic</h3>
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Seed a topic yourself with a short description and marker keywords that should match incoming mail.
        </p>
        <Input
          label="Title"
          aria-label="Manual topic title"
          value={createTitle}
          onChange={(event) => setCreateTitle(event.target.value)}
          placeholder="e.g. Home blog"
        />
        <div className="space-y-1">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">Description</label>
          <textarea
            aria-label="Manual topic description"
            value={createSummary}
            onChange={(event) => setCreateSummary(event.target.value)}
            rows={3}
            placeholder="Summarize what kind of mail belongs to this topic."
            className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
          />
        </div>
        <Input
          label="Marker keywords"
          aria-label="Manual topic marker keywords"
          value={createMarkerKeywords}
          onChange={(event) => setCreateMarkerKeywords(event.target.value)}
          placeholder="Comma-separated keywords, senders, or phrases"
        />
        <div className="flex justify-end">
          <Button
            type="button"
            onClick={() => createTopic.mutate()}
            isLoading={createTopic.isPending}
            disabled={!canCreateTopic}
            icon={<Plus size={16} />}
          >
            Save topic
          </Button>
        </div>
      </Card>

      {hasSelectedTopic && (
        <Card className="p-6 space-y-4 border-primary/20">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-primary">Selected topic</p>
              {isSelectedTopicLoading ? (
                <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                  <RefreshCw size={16} className="animate-spin" />
                  Loading topic details…
                </div>
              ) : selectedTopic ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-xl font-bold tracking-tight">{selectedTopic.title}</h3>
                    {selectedTopic.pinned && <Badge variant="primary">Pinned</Badge>}
                    <Badge variant={statusBadgeVariant(selectedTopic.status)}>{statusLabels[selectedTopic.status]}</Badge>
                    <Badge variant={selectedTopic.origin === 'manual' ? 'secondary' : 'gray'}>{selectedTopic.origin}</Badge>
                    {selectedTopic.confidence && <Badge variant="secondary">{selectedTopic.confidence}</Badge>}
                  </div>
                  {selectedTopic.summary && (
                    <p className="text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{selectedTopic.summary}</p>
                  )}
                  {selectedTopic.marker_keywords.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {selectedTopic.marker_keywords.map((keyword) => (
                        <Badge key={keyword} variant="gray">
                          {keyword}
                        </Badge>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-gray-400 dark:text-gray-500">
                    {selectedTopic.evidence_count} evidence item{selectedTopic.evidence_count === 1 ? '' : 's'} • Last seen{' '}
                    {formatServerDateTime(selectedTopic.last_seen_at)}
                  </p>
                </>
              ) : (
                <p className="text-sm text-amber-700 dark:text-amber-300">
                  {selectedTopicError ? 'This topic could not be loaded.' : 'No topic is currently selected.'}
                </p>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              {selectedTopic && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => confirmDeleteTopic(selectedTopic)}
                  isLoading={deleteTopic.isPending && deleteTopic.variables === selectedTopic.id}
                  icon={<Trash2 size={14} className="text-red-500" />}
                >
                  Delete
                </Button>
              )}
              <Button type="button" variant="ghost" size="sm" onClick={() => setSelectedTopicId(null)} icon={<X size={14} />}>
                Clear selection
              </Button>
            </div>
          </div>

          {selectedTopic && (
            <>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => updateTopic.mutate({ id: selectedTopic.id, data: { pinned: !selectedTopic.pinned } })}
                  isLoading={updateTopic.isPending}
                  icon={<Pin size={14} />}
                >
                  {selectedTopic.pinned ? 'Unpin' : 'Pin'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    updateTopic.mutate({
                      id: selectedTopic.id,
                      data: { status: selectedTopic.status === 'active' ? 'resolved' : 'active' },
                    })
                  }
                  isLoading={updateTopic.isPending}
                >
                  {selectedTopic.status === 'active'
                    ? 'Resolve'
                    : selectedTopic.status === 'candidate'
                      ? 'Confirm'
                      : 'Reactivate'}
                </Button>
              </div>

              {selectedTopic.origin === 'manual' && (
                <ManualTopicEditor
                  key={`${selectedTopic.id}:${selectedTopic.updated_at}`}
                  topic={selectedTopic}
                  isSaving={updateManualTopic.isPending}
                  onSave={(data) => updateManualTopic.mutate({ id: selectedTopic.id, data })}
                />
              )}

              <div className="space-y-3">
                <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Recent evidence</h4>
                {selectedTopic.evidence.length > 0 ? (
                  <div className="space-y-3">
                    {selectedTopic.evidence.slice(0, 3).map((evidence) => (
                      <div key={evidence.id} className="rounded-2xl border border-gray-100 dark:border-gray-800 p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="gray">{evidence.source_type}</Badge>
                          <Badge variant="secondary">{evidence.evidence_role}</Badge>
                        </div>
                        {evidence.excerpt && (
                          <p className="mt-2 text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{evidence.excerpt}</p>
                        )}
                        <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">{formatServerDateTime(evidence.created_at)}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 dark:text-gray-400">No supporting evidence has been captured yet.</p>
                )}
              </div>
            </>
          )}
        </Card>
      )}

      <Card className="p-6 space-y-4">
        <div className="flex flex-col md:flex-row gap-3 md:items-center md:justify-between">
          <div className="flex items-center gap-2">
            <Search size={18} className="text-primary" />
            <h3 className="text-lg font-bold">Tracked topics</h3>
          </div>
          <div className="flex flex-col md:flex-row gap-3 md:items-center">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search tracked topics"
              className="md:w-80"
            />
            <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
              <span>Status</span>
              <select
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as TopicStatus | 'all')}
                className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
              >
                {statusOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <RefreshCw className="animate-spin text-primary" />
          </div>
        ) : topics.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-8 text-center text-sm text-gray-500 dark:text-gray-400">
            No tracked topics match the current filter yet.
          </div>
        ) : (
          <div className="space-y-3">
            {topics.map((topic) => {
              const nextStatus: TopicStatus = topic.status === 'active' ? 'resolved' : 'active';
              const nextStatusLabel =
                topic.status === 'active' ? 'Resolve' : topic.status === 'candidate' ? 'Confirm' : 'Reactivate';
              return (
                <div key={topic.id} data-ui-item={`topic:${topic.id}`} className="rounded-2xl border border-gray-100 dark:border-gray-800 p-4">
                  <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-semibold text-sm text-gray-900 dark:text-gray-100">{topic.title}</p>
                        <Link
                          to={`/memory?tab=wiki&topic=${topic.id}`}
                          className="text-xs text-primary underline-offset-2 hover:underline"
                        >
                          Wiki page
                        </Link>
                        {topic.pinned && <Badge variant="primary">Pinned</Badge>}
                        <Badge variant={statusBadgeVariant(topic.status)}>{statusLabels[topic.status]}</Badge>
                        {topic.confidence && <Badge variant="secondary">{topic.confidence}</Badge>}
                      </div>
                      {topic.summary && (
                        <p className="mt-1 text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{topic.summary}</p>
                      )}
                      <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
                        {topic.evidence_count} evidence item{topic.evidence_count === 1 ? '' : 's'} • Last seen{' '}
                        {formatServerDateTime(topic.last_seen_at)}
                      </p>
                      {topic.status === 'candidate' && (
                        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                          Watching for now — Janus keeps this out of briefings and digests until more mail
                          mentions it, or until you confirm it.
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant={hasSelectedTopic && selectedTopic?.id === topic.id ? 'secondary' : 'outline'}
                        size="sm"
                        onClick={() => setSelectedTopicId(topic.id)}
                        icon={<ArrowRight size={14} />}
                      >
                        View details
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          updateTopic.mutate({ id: topic.id, data: { pinned: !topic.pinned } })
                        }
                        isLoading={updateTopic.isPending}
                        icon={<Pin size={14} />}
                      >
                        {topic.pinned ? 'Unpin' : 'Pin'}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          updateTopic.mutate({ id: topic.id, data: { status: nextStatus } })
                        }
                        isLoading={updateTopic.isPending}
                      >
                        {nextStatusLabel}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => confirmDeleteTopic(topic)}
                        isLoading={deleteTopic.isPending && deleteTopic.variables === topic.id}
                        aria-label={`Delete topic "${topic.title}"`}
                        icon={<Trash2 size={14} className="text-red-500" />}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {dialog}
    </div>
  );
};
