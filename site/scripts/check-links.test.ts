import { afterEach, beforeEach, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { brokenLinks } from './check-links';

let dist: string;

function page(path: string, body: string): void {
  mkdirSync(dirname(join(dist, path)), { recursive: true });
  writeFileSync(join(dist, path), `<!doctype html><html><body>${body}</body></html>`);
}

beforeEach(() => {
  dist = mkdtempSync(join(tmpdir(), 'dist-'));
  page('index.html', '<h2 id="install">Install</h2><a href="/guides/ci/">CI</a>');
  page('guides/ci/index.html', '<a href="/#install">back</a><a href="../../">home</a>');
  page('_astro/site.css', '');
});

afterEach(() => rmSync(dist, { recursive: true }));

test('a site whose every link resolves has no broken link', () => {
  page('reference/cli/index.html', '<link href="/_astro/site.css"><a href="https://fraiseql.dev/confiture/">c</a>');
  expect(brokenLinks(dist, 'https://semis.fraiseql.dev')).toEqual([]);
});

test('a link to a page the build does not hold is broken', () => {
  page('reference/cli/index.html', '<a href="/guides/scenarios/">scenarios</a>');
  expect(brokenLinks(dist, 'https://semis.fraiseql.dev')).toEqual([
    'reference/cli/index.html: /guides/scenarios/',
  ]);
});

test('a link to a heading the page does not have is broken', () => {
  page('reference/cli/index.html', '<a href="/guides/ci/#secrets">secrets</a>');
  expect(brokenLinks(dist, 'https://semis.fraiseql.dev')).toEqual([
    'reference/cli/index.html: /guides/ci/#secrets',
  ]);
});

test("a link to the site's own origin is checked as an internal one", () => {
  page('reference/cli/index.html', '<a href="https://semis.fraiseql.dev/missing/">m</a>');
  expect(brokenLinks(dist, 'https://semis.fraiseql.dev')).toEqual([
    'reference/cli/index.html: https://semis.fraiseql.dev/missing/',
  ]);
});

test("a page Astro builds as a file of its own, 404.html, resolves by its directory's URL", () => {
  page('404.html', '<link rel="canonical" href="https://semis.fraiseql.dev/404/">');
  expect(brokenLinks(dist, 'https://semis.fraiseql.dev')).toEqual([]);
});
