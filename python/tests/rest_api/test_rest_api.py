import json

from click.testing import CliRunner
from typing import Tuple
from unittest import TestCase

from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from http import HTTPStatus

from aac.execute.aac_execution_result import ExecutionStatus
from aac.execute.command_line import cli, initialize_cli
from aac.in_out.parser._parse_source import parse
from rest_api.rest_api_impl import plugin_name, rest_api, gen_openapi_spec
from rest_api.models.command_model import (
    CommandModel,
    CommandRequestModel,
    CommandResponseModel,
    to_command_model,
)
from rest_api.models.definition_model import DefinitionModel, to_definition_class, to_definition_model
from rest_api.models.file_model import FileModel, FilePathModel, FilePathRenameModel, to_file_model
from rest_api.aac_rest_app import app, refresh_available_files_in_workspace

class TestRestAPI(TestCase):

    test_client = TestClient(app)
    def test_get_available_commands(self):
        response = self.test_client.get("/commands")
        print(response)
        self.assertEqual(HTTPStatus.OK, response.status_code)

    def test_execute_validate_command(self):
        command_name = "check"
        test_model = parse(TEST_MODEL)

        request_arguments = CommandRequestModel(name=command_name, arguments=[test_model[0].name, "False", "False"])
        response = self.test_client.post("/command", data=json.dumps(jsonable_encoder(request_arguments)))

        self.assertEqual(HTTPStatus.OK, response.status_code)
        self.assertTrue(response.json().get("success"))
        self.assertIn("success", response.text)
        self.assertIn(command_name, response.text)
        self.assertIn(test_model.name, response.text)


TEST_MODEL = """
model:
    name: TestModel
    description: A TestModel
"""
