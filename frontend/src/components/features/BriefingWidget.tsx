import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, MessageSquare, RefreshCcw, RefreshCw, Sparkles } from 'lucide-react';
import { buildWebSocketUrl } from '../../lib/network';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { JanusMarkdown } from './JanusMarkdown';

type BriefingSocket = WebSocket & { janusIntentionalClose?: boolean };

interface BriefingWidgetProps {
  variant?: 'compact' | 'full';
  className?: string;
}

export const BriefingWidget: React.FC<BriefingWidgetProps> = ({ variant = 'compact', className = '' }) => {
  const [briefing, setBriefing] = useState('');
  const [isBriefingLoading, setIsBriefingLoading] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const briefingSocketRef = useRef<WebSocket | null>(null);
  const streamCompletedRef = useRef(false);

  // Opening the dashboard shows today's briefing (the Morning briefing trigger's
  // latest run); refreshing asks Janus to run that trigger again now.
  const startBriefing = useCallback((refresh: boolean = false) => {
    const previousSocket = briefingSocketRef.current as BriefingSocket | null;
    if (previousSocket) {
      previousSocket.janusIntentionalClose = true;
      previousSocket.close();
    }

    setIsBriefingLoading(true);
    setBriefing('');
    setConnectionError(null);
    streamCompletedRef.current = false;

    let timezone: string;
    try {
      timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch {
      timezone = '';
    }
    const params = new URLSearchParams();
    if (refresh) params.set('refresh', '1');
    if (timezone) params.set('timezone', timezone);
    const query = params.toString();
    const wsUrl = `${buildWebSocketUrl('/ws/briefing')}${query ? `?${query}` : ''}`;
    const socket = new WebSocket(wsUrl) as BriefingSocket;

    socket.onmessage = (event) => {
      if (event.data === '__START__') {
        setBriefing('');
      } else if (event.data === '__END__') {
        streamCompletedRef.current = true;
        setIsBriefingLoading(false);
      } else if (event.data.startsWith('__ERROR__:')) {
        const errorMsg = event.data.slice('__ERROR__:'.length);
        setIsBriefingLoading(false);
        setConnectionError(errorMsg || 'Janus encountered an error while generating your briefing.');
      } else {
        setBriefing((prev) => prev + event.data);
      }
    };

    socket.onclose = () => {
      if (briefingSocketRef.current !== socket) return;
      briefingSocketRef.current = null;
      setIsBriefingLoading(false);
      if (!streamCompletedRef.current && !socket.janusIntentionalClose) {
        setConnectionError((current) => current ?? 'Briefing disconnected. Retry to regenerate.');
      }
    };

    socket.onerror = () => {
      if (briefingSocketRef.current !== socket) return;
      setIsBriefingLoading(false);
      setConnectionError('Failed to connect to Janus AI. Retry to try again.');
    };

    briefingSocketRef.current = socket;
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      startBriefing();
    }, 0);

    return () => {
      window.clearTimeout(timer);
      const socket = briefingSocketRef.current as BriefingSocket | null;
      if (socket) {
        socket.janusIntentionalClose = true;
        socket.close();
      }
    };
  }, [startBriefing]);

  const isCompact = variant === 'compact';

  return (
    <Card className={className}>
      {connectionError && (
        <div
          className={`${
            isCompact ? 'mx-4 mt-4' : 'mx-6 mt-6'
          } rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-900/30 dark:bg-amber-900/20 dark:text-amber-300`}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} />
              <span>{connectionError}</span>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => startBriefing(true)}
              icon={<RefreshCw size={14} />}
            >
              Retry
            </Button>
          </div>
        </div>
      )}

      <div className={isCompact ? 'p-5 space-y-4' : 'p-8 space-y-6'}>
        <div className="flex items-center justify-between">
          <div className="space-y-2">
            <h2
              className={`${
                isCompact ? 'text-base' : 'text-sm'
              } font-black uppercase tracking-widest text-primary flex items-center gap-2`}
            >
              {isCompact ? <Sparkles size={16} /> : <MessageSquare size={16} />}
              {isCompact ? 'Briefing' : 'Janus Analysis'}
            </h2>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">Topic-aware</Badge>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Tracks durable threads alongside today&apos;s schedule and reminders.
              </p>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => startBriefing(true)}
            disabled={isBriefingLoading}
            aria-label="Refresh briefing"
            title="Refresh briefing"
            icon={<RefreshCcw size={18} className={isBriefingLoading ? 'animate-spin' : ''} />}
          />
        </div>

        <div
          className={`text-gray-700 dark:text-gray-300 font-medium whitespace-pre-wrap ${
            isCompact ? 'h-[260px] overflow-y-auto text-sm leading-6' : 'min-h-[220px] text-lg leading-relaxed'
          }`}
        >
          {briefing ? (
            <div className="prose prose-sm max-w-none dark:prose-invert prose-headings:text-primary prose-headings:font-semibold prose-p:my-2 prose-ul:my-2 prose-li:my-1">
              <JanusMarkdown>{briefing}</JanusMarkdown>
            </div>
          ) : !isBriefingLoading ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Janus will summarize your day around ongoing topics once the briefing starts.
            </p>
          ) : null}
          {isBriefingLoading && <span className="inline-block w-2 h-5 ml-1 bg-primary animate-pulse align-middle"></span>}
        </div>
      </div>
    </Card>
  );
};
