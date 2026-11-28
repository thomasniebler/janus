import React from 'react';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { EmbeddingConfig } from './EmbeddingConfig';
import { ImageGenerationConfig } from './ImageGenerationConfig';
import { WebSearchConfig } from './WebSearchConfig';

/**
 * The capabilities on Settings -> AI & Models -> Search & images: semantic search (JAN-241),
 * web search (JAN-211) and image generation (JAN-253). Each one picks a provider from the same
 * list as the default AI backend and reuses the key stored on the Models tab.
 */
export const CapabilitySettings: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => (
  <>
    <EmbeddingConfig />
    <ErrorBoundary title="Web search configuration crashed">
      <WebSearchConfig isAdmin={isAdmin} />
    </ErrorBoundary>
    <ImageGenerationConfig />
  </>
);
