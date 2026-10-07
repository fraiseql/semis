// Every internal link of a built site resolves: to a file the build holds and, when it
// names one, to a heading that page has. Usage: bun run scripts/check-links.ts dist [origin]
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, posix, relative } from 'node:path';

const LINK = /\s(?:href|src)="([^"]*)"/g;
const ID = /\sid="([^"]*)"/g;

function files(directory: string): string[] {
  return readdirSync(directory, { recursive: true, encoding: 'utf8' })
    .map((name) => join(directory, name))
    .filter((path) => statSync(path).isFile());
}

/** The file a path of the site is served from, or null when the build holds none. */
function served(dist: string, path: string): string | null {
  const bare = path.replace(/\/$/, '');
  const candidates = path.endsWith('/')
    ? [join(dist, bare, 'index.html'), join(dist, `${bare}.html`)]
    : [join(dist, path), join(dist, path, 'index.html'), join(dist, `${path}.html`)];
  return candidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile()) ?? null;
}

/** *path* as the site serves it, read from the page at *page*: `../` and `?query` resolved. */
function absolute(page: string, path: string): string {
  const resolved = posix.resolve(`/${posix.dirname(page)}/`, path.split('?')[0]);
  return path.endsWith('/') && resolved !== '/' ? `${resolved}/` : resolved;
}

function ids(html: string): Set<string> {
  return new Set([...html.matchAll(ID)].map((match) => match[1]));
}

/** Each internal link that does not resolve, as `page: link`. */
export function brokenLinks(dist: string, origin: string): string[] {
  const broken: string[] = [];
  for (const file of files(dist).filter((path) => path.endsWith('.html'))) {
    const page = relative(dist, file);
    const html = readFileSync(file, 'utf8');
    for (const [, link] of html.matchAll(LINK)) {
      const local = link.startsWith(origin) ? link.slice(origin.length) || '/' : link;
      if (/^[a-z][a-z0-9+.-]*:/i.test(local) || local.startsWith('//')) continue;
      const [path, anchor] = local.split('#', 2);
      const target = path === '' ? file : served(dist, absolute(page, path));
      const resolves =
        target !== null &&
        (!anchor || !target.endsWith('.html') || ids(readFileSync(target, 'utf8')).has(decodeURIComponent(anchor)));
      if (!resolves) broken.push(`${page}: ${link}`);
    }
  }
  return broken.sort();
}

if (import.meta.main) {
  const [dist = 'dist', origin = 'https://semis.fraiseql.dev'] = process.argv.slice(2);
  const broken = brokenLinks(dist, origin);
  const pages = files(dist).filter((path) => path.endsWith('.html')).length;
  for (const line of broken) console.error(`broken link: ${line}`);
  console.log(`${pages} pages checked, ${broken.length} broken internal links`);
  if (broken.length > 0 || pages === 0) process.exit(1);
}
