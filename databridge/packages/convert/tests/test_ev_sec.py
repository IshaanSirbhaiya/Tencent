"""EV-SEC canaries (09_FINAL_PRD §21.9): a billion-laughs YAML and an XXE XML MUST be
REFUSED. These join the C24 security suite; they must pass from the moment the adapter
exists. A green run here = the attack was blocked."""
from __future__ import annotations

import pytest

from databridge_convert.adapters import xml as xml_adapter
from databridge_convert.adapters import yaml as yaml_adapter
from databridge_convert.adapters._util import AdapterError

BILLION_LAUGHS = b"""
a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]
e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]
f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]
g: &g [*f,*f,*f,*f,*f,*f,*f,*f,*f]
"""

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<root>&xxe;</root>"""


# A DEEP bomb with ~38 alias refs — under the old count cap of 50, but exponential in
# depth (2^19). The tightened cap (30) + node backstop must refuse it (review finding).
DEEP_BOMB = b"a0: &a0 [x, x]\n" + b"\n".join(
    f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]".encode() for i in range(1, 20))


def test_billion_laughs_yaml_is_refused():
    with pytest.raises(AdapterError):
        yaml_adapter.read(BILLION_LAUGHS)


def test_deep_alias_bomb_under_old_cap_is_refused():
    with pytest.raises(AdapterError):
        yaml_adapter.read(DEEP_BOMB)


def test_xxe_xml_is_refused():
    with pytest.raises(AdapterError):
        xml_adapter.read(XXE)


def test_normal_yaml_parses():
    tr = yaml_adapter.read(b"name: Ana\nage: 30\n")
    assert tr.root == {"name": "Ana", "age": 30}


def test_normal_xml_parses():
    tr = xml_adapter.read(b"<person><name>Ana</name></person>")
    assert tr.root == {"person": {"name": "Ana"}}
