import type { StorybookConfig } from '@storybook/react-vite';

const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(ts|tsx)'],
  // A sample PNG at the authenticated image path, so the ImageBlock stories can show a
  // loaded image (JAN-253); the real /api/images/<id> is served by the backend.
  staticDirs: [{ from: './static/images', to: '/api/images' }],
  framework: {
    name: '@storybook/react-vite',
    options: {},
  },
};

export default config;
