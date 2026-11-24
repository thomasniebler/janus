import { describe, expect, it } from 'vitest';
import { googleOAuthErrorMessage } from './googleOAuthErrors';

describe('googleOAuthErrorMessage', () => {
  it('explains each specific callback error code (JAN-318)', () => {
    expect(googleOAuthErrorMessage('google_redirect_uri_mismatch')).toContain('redirect URI');
    expect(googleOAuthErrorMessage('google_scope_missing')).toContain('Calendar and Tasks');
    expect(googleOAuthErrorMessage('google_invalid_client')).toContain('Client Secret');
  });

  it('falls back to the raw code for unknown errors', () => {
    expect(googleOAuthErrorMessage('something_new')).toBe('OAuth2 error: something_new');
  });
});
