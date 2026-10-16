"""Drift detection: does a router's running config still equal the config intent rendered?

Both texts are reduced to a SET of context-qualified lines, e.g.

    ("router bgp 65010", "address-family ipv4 unicast", "network 10.20.10.0/24")

using FRR's own indentation (one space per nesting level). Comparing sets makes the check
insensitive to the order FRR prints blocks in, while a line that moved to a different
block still counts as a difference. Lines FRR adds on its own (version banner, `end`,
`exit`, forwarding state) are ignored by an explicit, short list - everything else that
differs is drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

IGNORE = [
    re.compile(p)
    for p in (
        r"^Building configuration",
        r"^Current configuration",
        r"^frr version ",
        r"^frr defaults ",
        r"^end$",
        r"^exit$",
        r"^exit-address-family$",
        r"^exit-vrf$",
        r"^!",
        r"^service integrated-vtysh-config$",
        r"^(no )?ipv6 forwarding$",
        r"^(no )?ip forwarding$",
    )
]

# Block headers that may legitimately appear with no children (FRR prints e.g. an empty
# `interface eth0` once zebra has seen the interface). An EMPTY one is not drift.
BLOCK_HEADERS = re.compile(r"^(interface|router|route-map|address-family|vrf|line|segment-routing|mpls)( |$)")

Item = tuple[str, ...]


def items(config: str) -> set[Item]:
    """Context-qualified leaf lines of an FRR config."""
    raw = []
    for line in config.splitlines():
        if not line.strip():
            continue
        text = line.strip()
        if any(p.search(text) for p in IGNORE):
            continue
        indent = len(line) - len(line.lstrip(" "))
        raw.append((indent, text))
    out: set[Item] = set()
    stack: list[tuple[int, str]] = []
    for idx, (indent, text) in enumerate(raw):
        while stack and stack[-1][0] >= indent:
            stack.pop()
        nxt_indent = raw[idx + 1][0] if idx + 1 < len(raw) else -1
        is_parent = nxt_indent > indent
        if is_parent:
            stack.append((indent, text))
            continue
        if BLOCK_HEADERS.match(text):
            continue  # empty block header
        out.add(tuple(t for _, t in stack) + (text,))
    return out


def fmt(item: Item) -> str:
    return " > ".join(item)


@dataclass
class Drift:
    router: str
    missing: list[str] = field(default_factory=list)  # in intent, not on the box
    extra: list[str] = field(default_factory=list)  # on the box, not in intent

    @property
    def clean(self) -> bool:
        return not self.missing and not self.extra

    def as_dict(self) -> dict:
        return {"router": self.router, "clean": self.clean, "missing": self.missing, "extra": self.extra}


def diff(router: str, intended: str, running: str) -> Drift:
    want, have = items(intended), items(running)
    return Drift(
        router=router,
        missing=sorted(fmt(i) for i in want - have),
        extra=sorted(fmt(i) for i in have - want),
    )


def remediation(drift: Drift) -> list[list[str]]:
    """vtysh command sequences that move the running config back to intent.

    Used only as a fallback when FRR's own frr-reload.py is unavailable or leaves drift.
    """
    cmds: list[list[str]] = []
    for line in drift.extra:
        *ctx, leaf = line.split(" > ")
        undo = leaf[3:] if leaf.startswith("no ") else f"no {leaf}"
        cmds.append(["configure terminal", *ctx, undo, "end"])
    for line in drift.missing:
        *ctx, leaf = line.split(" > ")
        cmds.append(["configure terminal", *ctx, leaf, "end"])
    return cmds
