import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      css: true,
      isolate: false,
      // Coverage (JAN-261): every production source file counts, imported by a test or
      // not; tests, stories, fixtures, test helpers and generated types do not.
      coverage: {
        provider: 'v8',
        include: ['src/**/*.{ts,tsx}'],
        exclude: [
          'src/**/*.{test,spec}.{ts,tsx}',
          'src/**/*.stories.tsx',
          'src/**/*Fixtures.ts',
          'src/test/**',
          'src/generated/**',
          'src/**/*.d.ts',
        ],
        reporter: ['text-summary', 'json-summary', 'json', 'html', 'cobertura'],
        reportsDirectory: './coverage',
        // The measured main baseline rounded down (JAN-261). Raise them when coverage
        // improves; never lower them to make a PR pass.
        thresholds: { statements: 61, branches: 57, functions: 51, lines: 62 },
      },
    },
  })
);
