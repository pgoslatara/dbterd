import dataclasses
import json

import pytest

from dbterd.helpers import jsonify


class Dummy:
    def __init__(self, str, secret_str) -> None:
        self.json_str = str
        self.secret_json_str = secret_str


@dataclasses.dataclass
class DummyData:
    json_str: str
    secret: str


class TestFile:
    @pytest.mark.parametrize(
        "input, output",
        [
            ('{"data":"dummy"}', {"data": "dummy"}),
            (
                '{"password":"this is a secret password"}',
                {"password": "this " + "*" * 10},
            ),
        ],
    )
    def test_mask(self, input, output):
        assert jsonify.mask(obj=input) == output

    def test_mask_with_class(self):
        obj = Dummy(str="dummy", secret_str="this is a secret")
        assert jsonify.mask(json.dumps(obj.__dict__, cls=jsonify.EnhancedJSONEncoder)) == {
            "json_str": "dummy",
            "secret_json_str": "this " + "*" * 10,
        }

    def test_to_json_none(self):
        assert jsonify.to_json(obj=None) == {}

    @pytest.mark.parametrize(
        "input",
        [
            ({"data": {"child_data": "dummy"}}),
            ({"data": "dummy"}),
            ({"data": {}}),
        ],
    )
    def test_to_json_has_pretty_format(self, input):
        assert jsonify.to_json(obj=input) == json.dumps(
            input,
            indent=4,
            cls=jsonify.EnhancedJSONEncoder,
        )
