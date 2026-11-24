import React from 'react';
import { AlertTriangle, RotateCcw, Undo2 } from 'lucide-react';
import { Button } from './Button';
import { Card } from './Card';
import {
  clearStoredUIVersion,
  DEFAULT_UI_VERSION,
  UI_VERSION_QUERY_PARAM,
} from '../../contexts/uiVersion';

interface ErrorBoundaryProps {
  children: React.ReactNode;
  title?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  override componentDidCatch(error: Error): void {
    console.error('ErrorBoundary caught an error', error);
  }

  private handleReload = () => {
    window.location.reload();
  };

  /**
   * Last-resort kill switch: drops the cached UI shell preference and reloads with the
   * `?ui=v1` override so the classic shell renders even if the stored/server preference
   * still names another shell. Works from anywhere, including a crashed alternate shell,
   * and stays correct however many shells exist because it always targets the default.
   */
  private handleResetToClassicUI = () => {
    clearStoredUIVersion();
    const url = new URL(window.location.href);
    url.searchParams.set(UI_VERSION_QUERY_PARAM, DEFAULT_UI_VERSION);
    window.location.assign(url.toString());
  };

  override render() {
    if (this.state.hasError) {
      return (
        <Card className="p-6 border-red-100 dark:border-red-900/30">
          <div className="flex flex-col items-start gap-4 text-sm">
            <div className="flex items-center gap-2 text-red-600 dark:text-red-400 font-bold">
              <AlertTriangle size={18} />
              {this.props.title || 'This section crashed'}
            </div>
            <p className="text-gray-600 dark:text-gray-400">
              Reload the page to recover this view. If the problem started after switching the app
              appearance, reset to the classic UI.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={this.handleReload} icon={<RotateCcw size={14} />}>
                Reload
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={this.handleResetToClassicUI}
                icon={<Undo2 size={14} />}
              >
                Reset to classic UI (v1)
              </Button>
            </div>
          </div>
        </Card>
      );
    }

    return this.props.children;
  }
}
