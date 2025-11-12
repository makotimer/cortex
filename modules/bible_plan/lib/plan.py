# modules/bible_plan/lib/plan.py

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

PLAN_FILE = "chapter_plan.json"


@dataclass(frozen=True)
class PlanItem:
    book: str
    chapter: int


_PLAN_RE = re.compile(r"^\s*(?P<book>.+?)\s+(?P<chap>\d+)\s*$")

_SINGLE_CHAPTER_BOOKS_CANON = {"Obadiah", "Philemon", "2 John", "3 John", "Jude"}


def _normalize_book_name(s: str) -> str:
    s = " ".join(s.strip().split())
    m = re.match(r"^(\d)\s+(.*)$", s)
    if m:
        return f"{m.group(1)} {m.group(2).title()}"
    return s.title()


def resolve_plan_dir(default_pkg_dir: str | None) -> str:
    """
    Resolution order (first match wins):
      1) Explicit default_pkg_dir argument (if it contains chapter_plan.json)
      2) Directory next to this file (robust even under pytest tmp import paths)
      3) importlib.resources.files(__package__) (installed package data)
      4) Project fallbacks (useful in dev containers)
    """

    def has_plan(p: os.PathLike | str | None) -> bool:
        return bool(p) and (Path(p) / PLAN_FILE).exists()

    # 1) The caller's suggested directory (if provided **and** valid)
    if has_plan(default_pkg_dir):
        return str(default_pkg_dir)

    # 2) This file's directory (stable, not affected by pytest CWD)
    here = Path(__file__).resolve().parent
    if has_plan(here):
        return str(here)

    # 3) Package data (if chapter_plan.json is packaged)
    try:
        pkg_base = resources.files(__package__)
        if has_plan(pkg_base):
            return str(pkg_base)
    except Exception:
        pass

    # 4) Dev/compose fallbacks (tune to your project layout as needed)
    for cand in (
        Path.cwd() / "local" / "config" / "bible_plan",
        Path("/app/modules/bible_plan"),
    ):
        if has_plan(cand):
            return str(cand)

    raise ValueError(f"{PLAN_FILE} not found.")


def _load_from_dir(dirpath: str) -> list[PlanItem]:
    path = os.path.join(dirpath, PLAN_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise ValueError(f"{PLAN_FILE} missing at {path}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {PLAN_FILE}: {e}") from e

    if not isinstance(data, list) or not data:
        raise ValueError(f"{PLAN_FILE} must be a non-empty array of 'Book N' strings (or single-chapter 'Book').")

    out: list[PlanItem] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, str):
            raise ValueError(f"Plan item #{i} must be a string, got {type(raw).__name__}")

        m = _PLAN_RE.match(raw)
        if m:
            book = " ".join(m.group("book").strip().split())
            chap = int(m.group("chap"))
            if chap < 1:
                raise ValueError(f"Invalid chapter at item #{i}: {chap}")
            out.append(PlanItem(book, chap))
            continue

        norm_book = _normalize_book_name(raw)
        if norm_book in _SINGLE_CHAPTER_BOOKS_CANON:
            out.append(PlanItem(norm_book, 1))
            continue

        raise ValueError(f"Plan item #{i} not in 'Book N' form or single-chapter 'Book': {raw!r}")

    return out


def load_plan(pkg_dir: str | None = None) -> list[PlanItem]:
    """
    Passes through an optional pkg_dir hint, but resolution no longer trusts pytest's CWD.
    """
    dirpath = resolve_plan_dir(pkg_dir)
    return _load_from_dir(dirpath)
