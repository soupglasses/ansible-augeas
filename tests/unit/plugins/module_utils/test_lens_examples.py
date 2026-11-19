# Copyright (c) 2026, Sofie Finnes <sofie+git@finnes.dev>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ansible_collections.soupglasses.augeas.plugins.module_utils.resource import (
    AugeasResource,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "lens_examples"
README = Path(__file__).resolve().parents[4] / "README.md"
CASES = sorted(EXAMPLES.glob("*.yml"))

assert CASES, "the lens example catalog must not be empty"


def test_readme_links_every_lens_example():
    readme = README.read_text(encoding="utf-8")

    for case_file in CASES:
        assert f"tests/lens_examples/{case_file.name}" in readme


def _load_case(case_file):
    with case_file.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _run(case, root, check_mode=False):
    task = case["task"]
    resource = AugeasResource(
        file=case["file"],
        lens=case["lens"],
        path=task.get("path", ""),
        check_mode=check_mode,
        root=str(root),
    )
    try:
        return resource.run(task.get("state", "merged"), task.get("config"))
    finally:
        resource.close()


@pytest.mark.parametrize("case_file", CASES, ids=lambda path: path.stem)
def test_lens_example(case_file, tmp_path):
    case = _load_case(case_file)
    target = tmp_path / case["file"].lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(case["before"], encoding="utf-8")

    preview = _run(case, tmp_path, check_mode=True)
    assert preview["changed"] is True
    assert target.read_text(encoding="utf-8") == case["before"]

    applied = _run(case, tmp_path)
    assert applied["changed"] is True
    assert target.read_text(encoding="utf-8") == case["after"]

    repeated = _run(case, tmp_path)
    assert repeated["changed"] is False
    assert target.read_text(encoding="utf-8") == case["after"]
