import React, { useRef, useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, FileText, Trash2, Upload } from 'lucide-react';
import { knowledgeApi } from '../../api';
import type { KnowledgeSource, KnowledgeSourceStatus, KnowledgeSourceType } from '../../types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';

const TYPE_LABELS: Record<KnowledgeSourceType, string> = {
  email: 'Email',
  email_attachment: 'Attachment',
  conversation: 'Chat',
  whatsapp_conversation: 'WhatsApp',
  calendar_event: 'Calendar',
  planner_task: 'Task',
  upload: 'Upload',
};

const STATUS_VARIANTS: Record<KnowledgeSourceStatus, 'success' | 'warning' | 'gray' | 'danger'> = {
  ready: 'success',
  pending: 'warning',
  skipped: 'gray',
  failed: 'danger',
};

/** Sources built from files; other types are rebuilt from their owner row, so deleting them here would not stick. */
const DELETABLE_TYPES: ReadonlySet<KnowledgeSourceType> = new Set(['upload', 'email_attachment']);

const ACCEPTED_FILES = '.pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.html,.htm,.eml';

const formatSize = (bytes?: number | null) => {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const errorMessage = (error: unknown) =>
  axios.isAxiosError(error) ? (error.response?.data?.detail ?? error.message) : 'Upload failed.';

/** Knowledge sources (JAN-242): upload documents and see what Janus has ingested from mail, chats, and files. */
export const KnowledgeSourcesManager: React.FC = () => {
  const queryClient = useQueryClient();
  const { confirm, dialog } = useConfirmDialog();
  const fileInput = useRef<HTMLInputElement>(null);
  const [typeFilter, setTypeFilter] = useState<KnowledgeSourceType | ''>('');
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data: sources = [], isLoading, isError } = useQuery<KnowledgeSource[]>({
    queryKey: ['knowledge-sources', typeFilter],
    queryFn: async () => (await knowledgeApi.listSources({ sourceType: typeFilter || undefined })).data,
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => knowledgeApi.upload(file),
    onMutate: () => setUploadError(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['knowledge-sources'] }),
    onError: (error) => setUploadError(errorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => knowledgeApi.deleteSource(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['knowledge-sources'] }),
  });

  const onFileChosen = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) uploadMutation.mutate(file);
    event.target.value = '';
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight flex items-center gap-3">
            <FileText className="text-primary" />
            Documents
          </h2>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Everything Janus has read into its knowledge: your mail and its attachments, chats, WhatsApp, calendar,
            tasks, and files you upload (PDF, Word, Excel, PowerPoint, text, CSV, HTML, mail; up to 10 MB).
          </p>
        </div>
        <div>
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPTED_FILES}
            className="hidden"
            aria-label="Document file"
            onChange={onFileChosen}
          />
          <Button
            type="button"
            onClick={() => fileInput.current?.click()}
            isLoading={uploadMutation.isPending}
            icon={<Upload size={16} />}
          >
            Upload document
          </Button>
        </div>
      </div>

      {uploadError && (
        <div
          className="p-4 rounded-2xl text-sm flex items-center gap-3 bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400"
          role="alert"
        >
          <AlertCircle size={20} />
          <p className="font-medium">{uploadError}</p>
        </div>
      )}

      <Card className="p-6 space-y-4">
        <div className="flex items-center justify-between gap-4">
          <h3 className="text-lg font-bold">Sources</h3>
          <label className="text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2">
            Type
            <select
              aria-label="Source type"
              value={typeFilter}
              onChange={(event) => setTypeFilter(event.target.value as KnowledgeSourceType | '')}
              className="rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-dark-bg px-3 py-1.5 text-sm"
            >
              <option value="">All</option>
              {(Object.keys(TYPE_LABELS) as KnowledgeSourceType[]).map((type) => (
                <option key={type} value={type}>
                  {TYPE_LABELS[type]}
                </option>
              ))}
            </select>
          </label>
        </div>

        {isLoading ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">Loading sources…</p>
        ) : isError ? (
          <p className="text-sm text-red-600 dark:text-red-400 flex items-center gap-2" role="alert">
            <AlertCircle size={16} />
            Could not load the sources. Reload the page to try again.
          </p>
        ) : sources.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Nothing ingested yet. New mail and chats appear here within a minute; upload a document to add it now.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800" aria-label="Knowledge sources">
            {sources.map((source) => (
              <li key={source.id} className="py-3 flex items-start justify-between gap-4">
                <div className="min-w-0 space-y-1">
                  <p className="font-medium truncate">{source.title || '(untitled)'}</p>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                    <Badge variant="gray">{TYPE_LABELS[source.source_type] ?? source.source_type}</Badge>
                    <Badge variant={STATUS_VARIANTS[source.status]}>{source.status}</Badge>
                    {source.status === 'ready' && (
                      <span>
                        {source.chunk_count} chunk{source.chunk_count === 1 ? '' : 's'}
                      </span>
                    )}
                    {source.size_bytes != null && <span>{formatSize(source.size_bytes)}</span>}
                  </div>
                  {source.status_detail && (
                    <p className="text-xs text-gray-500 dark:text-gray-400">{source.status_detail}</p>
                  )}
                </div>
                {DELETABLE_TYPES.has(source.source_type) && (
                  <Button
                    type="button"
                    variant="ghost"
                    aria-label={`Delete ${source.title || 'source'}`}
                    onClick={() =>
                      confirm({
                        title: 'Delete source?',
                        message: `Delete "${source.title || 'this source'}" and its extracted text?`,
                        confirmLabel: 'Delete',
                        tone: 'warning',
                        onConfirm: () => deleteMutation.mutate(source.id),
                      })
                    }
                    icon={<Trash2 size={16} />}
                  />
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
      {dialog}
    </div>
  );
};
