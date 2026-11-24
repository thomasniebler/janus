/**
 * Messages for the `error` codes the Google and Microsoft OAuth callbacks
 * (`/api/connections/<provider>/callback`) appends to the Settings redirect (JAN-318).
 * Each code names one cause, so the user knows what to fix.
 */
const GOOGLE_OAUTH_ERROR_MESSAGES: Record<string, string> = {
  google_denied: 'Google authorization was denied.',
  google_invalid_callback: 'Invalid OAuth2 callback parameters.',
  google_state_invalid: 'OAuth2 state token expired or invalid. Please try again.',
  google_not_configured:
    'Google is not configured. Set google_client_id and google_client_secret in settings.toml and restart Janus.',
  google_invalid_grant: 'Google rejected the authorization code (expired or already used). Please connect again.',
  google_invalid_client:
    'Google rejected the Client ID or Client Secret. Check google_client_id and google_client_secret in settings.toml.',
  google_redirect_uri_mismatch:
    'The redirect URI is not registered for this Google OAuth client. Add the redirect URIs shown on the Connections tab in Google Cloud Console.',
  google_unreachable: 'Janus could not reach Google. Check the server network connection and try again.',
  google_scope_missing: 'Calendar and Tasks access are both required. Connect again and allow every requested permission.',
  google_no_refresh_token:
    'Google did not return a refresh token. Remove Janus at myaccount.google.com/permissions, then connect again.',
  google_account_save_failed: 'The Google connection could not be saved. Check the server log and try again.',
  microsoft_denied: 'Microsoft authorization was denied.',
  microsoft_invalid_callback: 'Invalid Microsoft sign-in callback parameters.',
  microsoft_state_invalid: 'The Microsoft sign-in expired or is invalid. Please try again.',
  microsoft_not_configured:
    'Microsoft is not configured. Set microsoft_client_id and microsoft_tenant_id in settings.toml and restart Janus.',
  microsoft_flow_failed: 'Janus could not start the Microsoft sign-in. Check microsoft_client_id and microsoft_tenant_id.',
  microsoft_token_exchange_failed:
    'Microsoft rejected the sign-in. Check the redirect URI and client id on the Connections tab, then try again.',
  google_token_exchange_failed: 'Failed to exchange authorization code for tokens. Check the server log for the cause.',
};

/** Returns the message for a Google OAuth callback error code. */
export function googleOAuthErrorMessage(code: string): string {
  return GOOGLE_OAUTH_ERROR_MESSAGES[code] ?? `OAuth2 error: ${code}`;
}
