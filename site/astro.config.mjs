// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  site: 'https://semis.fraiseql.dev',

  integrations: [
    starlight({
      title: 'semis',
      description: 'Reproducible seed and test data for PostgreSQL trinity-pattern schemas',
      logo: {
        light: './src/assets/logo-light.svg',
        dark: './src/assets/logo-dark.svg',
      },
      components: {
        SiteTitle: './src/components/SiteTitle.astro',
      },
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/fraiseql/semis' }],
      customCss: [
        './src/styles/global.css',
        './src/styles/fraiseql-theme.css',
        '@fontsource/inter/400.css',
        '@fontsource/inter/500.css',
        '@fontsource/inter/600.css',
        '@fontsource/inter/700.css',
        '@fontsource/jetbrains-mono/400.css',
        '@fontsource/jetbrains-mono/500.css',
      ],
      head: [{ tag: 'meta', attrs: { name: 'theme-color', content: '#e03131' } }],
      editLink: {
        baseUrl: 'https://github.com/fraiseql/semis/edit/main/site/',
      },
      sidebar: [
        { label: 'Overview', slug: 'index' },
        { label: 'Getting started', slug: 'getting-started' },
        {
          label: 'Concepts',
          items: [
            { label: 'The trinity pattern', slug: 'concepts/trinity-pattern' },
            { label: 'The semantic UUID', slug: 'concepts/semantic-uuid' },
            { label: 'The two FK modes', slug: 'concepts/fk-modes' },
            { label: 'The row contract', slug: 'concepts/row-contract' },
            { label: 'Determinism', slug: 'concepts/determinism' },
            { label: 'Schema pins', slug: 'concepts/schema-pins' },
          ],
        },
        {
          label: 'Guides',
          items: [
            { label: 'Writing scenarios', slug: 'guides/scenarios' },
            { label: 'Writing a provider library', slug: 'guides/provider-libraries' },
            { label: 'Validating seeds', slug: 'guides/validating-seeds' },
            { label: 'semis in CI', slug: 'guides/ci' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'The semis command', slug: 'reference/cli' },
            { label: 'semis.yaml', slug: 'reference/semis-yaml' },
            { label: 'The scenario file', slug: 'reference/scenario-file' },
            { label: 'Shipped provider libraries', slug: 'reference/providers' },
            { label: 'Exit codes and errors', slug: 'reference/exit-codes' },
            { label: 'Python API', slug: 'reference/python-api' },
          ],
        },
        { label: 'Changelog', slug: 'changelog' },
      ],
      expressiveCode: {
        themes: ['github-dark', 'github-light'],
        styleOverrides: {
          borderRadius: '0.5rem',
          codeFontFamily: "'JetBrains Mono', monospace",
          codeFontSize: '0.875rem',
          codeLineHeight: '1.7',
        },
      },
      pagination: true,
    }),
  ],

  vite: {
    // @ts-expect-error - @tailwindcss/vite Plugin[] is compatible at runtime
    plugins: tailwindcss(),
  },
});
