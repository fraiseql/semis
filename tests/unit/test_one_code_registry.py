"""codes.py is the only module that maps a table name to a table_code.

A code derived at two sites is a code that drifts, and every UUID semis wrote is a
hostage to it. Two shapes give a second site away: a literal mapping of names to
integers, and a code derived by hashing.
"""

import ast

from tests.unit.guards import imported_roots, package_modules

OWNER = "codes.py"

# Modules allowed a shape the guard flags, each with its reason. An entry that no
# longer flags anything is stale and fails.
EXEMPT: dict[str, str] = {
    "pin.py": "hashes the schema facts a scenario reads into its pin; it derives no code",
}

_HASHING_MODULES = {"hashlib", "zlib", "binascii"}


def _second_registry_sites(tree: ast.Module) -> list[str]:
    found: list[str] = [
        f"imports {root}" for root in sorted(imported_roots(tree) & _HASHING_MODULES)
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and isinstance(value, ast.Constant)
            and type(value.value) is int
            for key, value in zip(node.keys, node.values, strict=True)
        ):
            found.append(f"line {node.lineno}: a literal name → integer mapping")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "hash"
        ):
            found.append(f"line {node.lineno}: hash()")
    return found


def test_no_module_but_codes_maps_a_table_to_a_code() -> None:
    offenders = {
        name: sites
        for name, tree in package_modules().items()
        if name != OWNER and name not in EXEMPT and (sites := _second_registry_sites(tree))
    }
    assert offenders == {}


def test_exemptions_are_not_stale() -> None:
    modules = package_modules()
    stale = [name for name in EXEMPT if not _second_registry_sites(modules[name])]
    assert stale == []


def test_guard_sees_a_second_registry() -> None:
    literal = ast.parse('CODES = {"catalog.tb_city": 0x0A}')
    hashed = ast.parse("import zlib\ncode = zlib.crc32(b'catalog.tb_city')")
    builtin = ast.parse("code = hash('catalog.tb_city') & 0xFFFFFFFF")
    assert all(_second_registry_sites(tree) for tree in (literal, hashed, builtin))
