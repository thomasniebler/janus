import { setProjectAnnotations } from '@storybook/react';
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import preview from './storybookPreview';

setProjectAnnotations(preview);

afterEach(() => {
  cleanup();
});
