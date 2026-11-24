import React from 'react';
import { EmbeddingConfig } from './EmbeddingConfig';
import { ImageGenerationConfig } from './ImageGenerationConfig';
import { SurfaceModelConfig } from './SurfaceModelConfig';

/**
 * The model choices below the default AI backend on Settings -> AI & System: models per
 * surface (JAN-233), semantic search (JAN-241), and image generation (JAN-253). Each card
 * reads and saves the shared `system-settings` query on its own.
 */
export const AdditionalModelSettings: React.FC = () => (
  <>
    <SurfaceModelConfig />
    <EmbeddingConfig />
    <ImageGenerationConfig />
  </>
);
