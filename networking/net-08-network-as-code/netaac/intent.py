"""Load the source of truth, and apply a PR-style change to a copy of it.

PyYAML silently keeps the LAST value when a mapping repeats a key, so two routers called
`br1-r1` (or an interface listed twice) would quietly become one. The loader here refuses
duplicate keys instead; the validator reports them as DUPLICATE_KEY.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml


class DuplicateKeyError(ValueError):
    pass


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: dict = {}
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise DuplicateKeyError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen[key] = True
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def loads(text: str) -> dict:
    data = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - SafeLoader subclass
    if not isinstance(data, dict):
        raise ValueError("source of truth must be a YAML mapping")
    return data


def load(path: str | Path) -> dict:
    return loads(Path(path).read_text(encoding="utf-8"))


def _merge(base: dict, patch: dict) -> dict:
    """Recursive dict merge: dicts merge, anything else replaces, None deletes the key."""
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def apply_change(base: dict, change: dict) -> dict:
    """Apply a change file ({merge: {...}, append: {list_key: [...]}}) to a COPY of base."""
    result = _merge(base, change.get("merge") or {})
    for key, items in (change.get("append") or {}).items():
        current = result.get(key)
        if current is None:
            current = []
        if not isinstance(current, list):
            raise ValueError(f"append target {key!r} is not a list")
        result[key] = current + copy.deepcopy(items)
    return result
