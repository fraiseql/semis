// The version this site describes: the package's own, read from pyproject.toml at build
// time, so a site built from a release tag names that release.
import { readFileSync } from 'node:fs';

const pyproject = readFileSync(new URL('../../../pyproject.toml', import.meta.url), 'utf8');
const found = /^\[project\][^[]*?^version = "([^"]+)"/ms.exec(pyproject);
if (found === null) throw new Error('pyproject.toml names no [project] version');

export const SEMIS_VERSION = found[1];
export const SEMIS_RELEASES_URL = 'https://github.com/fraiseql/semis/releases';
