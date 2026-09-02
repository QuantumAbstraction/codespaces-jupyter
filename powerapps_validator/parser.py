"""YAML source model retaining PyYAML AST nodes and source spans."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    root: Node | None
    error: yaml.YAMLError | None = None

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines(keepends=True)


def parse_document(text: str) -> ParsedDocument:
    try:
        return ParsedDocument(text, yaml.compose(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError as error:
        return ParsedDocument(text, None, error)


def items(node: MappingNode) -> Iterable[tuple[str, Node, ScalarNode]]:
    for key, value in node.value:
        if isinstance(key, ScalarNode):
            yield str(key.value), value, key


def walk(node: Node, path: str = "$") -> Iterable[tuple[MappingNode, str]]:
    if isinstance(node, MappingNode):
        yield node, path
        for key, value, _ in items(node):
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, SequenceNode):
        for index, value in enumerate(node.value):
            yield from walk(value, f"{path}[{index}]")
