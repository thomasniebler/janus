/** Google and Microsoft connections (Settings -> Connections) and the planner sync result. */
export type ConnectionProvider = 'google' | 'microsoft';

export interface ConnectionStatus {
  provider: ConnectionProvider;
  /** The operator put the OAuth client in settings.toml. */
  configured: boolean;
  connected: boolean;
  account_email?: string | null;
  /** The provider can back a mailbox at all (Outlook mail is not offered). */
  mail_supported: boolean;
  /** The sign-in grants mailbox access. */
  mail_ready: boolean;
  redirect_uris: string[];
  config_keys: string[];
}

export interface ConnectionsOverview {
  google: ConnectionStatus;
  microsoft: ConnectionStatus;
}

export interface SyncResult {
  synced_accounts: number;
  errors: string[];
}
