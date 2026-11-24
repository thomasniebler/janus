/** Cloud providers that can generate images (JAN-253); they reuse the stored chat credentials. */
export type ImageProviderId = 'openai' | 'gemini' | 'openrouter';

/** The image-generation fields of the system settings (JAN-253); a null provider means off. */
export interface ImageGenerationSettings {
  image_provider?: ImageProviderId | null;
  image_model?: string | null;
  image_default_models?: Record<ImageProviderId, string>;
  image_storage_limit_mb?: number;
}

/** The writable image-generation settings; null or empty turns image generation off. */
export interface ImageGenerationSettingsUpdate {
  image_provider?: ImageProviderId | null;
  image_model?: string | null;
}
