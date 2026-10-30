"""An explicit onboarding role introduction must not become the retrieval subject or an ACL."""
import pytest

from databridge_ib.onboarding import in_project, project_topic


@pytest.mark.parametrize("question,expected", [
    ("Hi, I'm Alex, the new product lead for Project Atlas. Bring me up to speed.", "Atlas"),
    ("I'm the incoming team lead for Project Boreal; make an onboarding brief.", "Boreal"),
    ("What is the status of Project Atlas?", None),
    ("I'm the new product lead. Bring me up to speed.", None),
    ("Show all employees working on Project Atlas.", None),
])
def test_scope_is_only_for_explicit_new_lead_brief(question, expected):
    assert project_topic(question) == expected


def test_passing_mention_is_not_a_project_scope():
    assert in_project({"title": "Project Atlas overview", "container_name": "Product"}, "Atlas")
    assert in_project({"title": "QA sign-off", "container_name": "Project Atlas"}, "Atlas")
    assert not in_project({"title": "Project Boreal", "container_name": "Boreal", "text": "Not Atlas"}, "Atlas")
    assert not in_project(None, "Atlas")
