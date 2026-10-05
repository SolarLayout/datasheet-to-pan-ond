"""Load datasheet specifications from YAML or JSON files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from datasheet_to_pan_ond.models import InverterDatasheet, ModuleDatasheet

M = TypeVar("M", bound=BaseModel)


def load_mapping(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return data


def load_model(path: str | Path, model: type[M]) -> M:
    return model.model_validate(load_mapping(path))


def load_module(path: str | Path) -> ModuleDatasheet:
    return load_model(path, ModuleDatasheet)


def load_inverter(path: str | Path) -> InverterDatasheet:
    return load_model(path, InverterDatasheet)


def dump_spec(spec: dict[str, Any], path: str | Path, header: list[str] | None = None) -> Path:
    """Write a spec mapping as YAML, with optional ``#`` comment header lines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)
    head = "".join(f"# {line}\n" for line in (header or []))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(head + body, encoding="utf-8")
    os.replace(tmp, path)
    return path
