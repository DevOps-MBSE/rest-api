import json
import os

from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from http import HTTPStatus

from aac.context.constants import DEFINITION_FIELD_NAME
from aac.in_out.parser._parse_source import parse
from rest_api.models.command_model import CommandRequestModel
from rest_api.models.definition_model import to_definition_model
from rest_api.models.file_model import FilePathModel, FilePathRenameModel
from rest_api.aac_rest_app import app, refresh_available_files_in_workspace


class TestRestApiCommands(TestCase):
    test_client = TestClient(app)

    def test_get_available_commands(self):
        response = self.test_client.get("/commands")
        self.assertEqual(HTTPStatus.OK, response.status_code)

    def test_execute_check_command(self):
        command_name = "check"
        test_model = parse(TEST_MODEL)[0]

        request_arguments = CommandRequestModel(name=command_name, arguments=[TEST_MODEL, "False", "False"])
        response = self.test_client.post("/command", data=json.dumps(jsonable_encoder(request_arguments)))

        self.assertEqual(HTTPStatus.OK, response.status_code)
        self.assertTrue(response.json().get("success"))
        self.assertIn("success", response.text)
        self.assertIn(command_name, response.text)
        self.assertIn(test_model.name, response.text)

    def test_execute_check_command_fails(self):
        command_name = "check"
        with self.assertRaises(Exception) as context:
            request_arguments = CommandRequestModel(name=command_name, arguments=[BAD_TEST_MODEL, "False", "False"])
            response = self.test_client.post("/command", data=json.dumps(jsonable_encoder(request_arguments)))


class TestAacRestApiFiles(TestCase):
    test_client = TestClient(app)

    def test_post_and_get_files(self):
        refresh_available_files_in_workspace()
        filepath = "tests/calc/model/calculator.yaml"
        self.assertTrue(os.path.isfile(filepath))

        file_model = [FilePathModel(uri=os.path.abspath(filepath))]
        self.test_client.post("/files/import", data=json.dumps(jsonable_encoder(file_model)))
        response = self.test_client.get("/files/context")
        self.assertEqual(HTTPStatus.OK, response.status_code)
        self.assertIn("calculator.yaml", response.text)

        available_files = self.test_client.get("/files/available")
        self.assertNotIn("calculator.yaml", available_files.text)

    def test_get_file_in_context_by_uri(self):
        filepath = "tests/calc/model/calculator.yaml"
        self.assertTrue(os.path.isfile(filepath))
        file_model = [FilePathModel(uri=os.path.abspath(filepath))]
        self.test_client.post("/files/import", data=json.dumps(jsonable_encoder(file_model)))

        response = self.test_client.get(f"/file?uri={os.path.abspath(filepath)}")
        self.assertIn(filepath, response.text)

    def test_rename_file_in_context(self):
        with TemporaryDirectory(dir=os.getcwd()) as temp_dir:
            old_file_name = "OldTestFile.yaml"
            new_file_name = "TestFile.aac"
            new_file_uri = os.path.join(temp_dir, new_file_name)

            temp_file_path = os.path.abspath(os.path.join(temp_dir, old_file_name))
            temp_file = open(temp_file_path, "w")
            temp_file.writelines(TEST_MODEL)
            temp_file.close()

            self.test_client.post("/files/import", data=json.dumps(jsonable_encoder([FilePathModel(uri=temp_file_path)])))
            rename_request_data = FilePathRenameModel(current_file_uri=temp_file_path, new_file_uri=new_file_uri)
            rename_response = self.test_client.put("/file", data=json.dumps(jsonable_encoder(rename_request_data)))
            self.assertEqual(HTTPStatus.NO_CONTENT, rename_response.status_code)

            response = self.test_client.get("/files/context")

            self.assertIn(new_file_uri, response.text)
            os.remove(new_file_uri)

    def test_remove_file_from_context(self):
        filepath = "tests/calc/model/calculator.yaml"
        self.assertTrue(os.path.isfile(filepath))
        file_model = [FilePathModel(uri=os.path.abspath(filepath))]
        self.test_client.post("/files/import", data=json.dumps(jsonable_encoder(file_model)))

        response = self.test_client.get("/files/context")
        self.assertEqual(HTTPStatus.OK, response.status_code)
        self.assertIn("calculator.yaml", response.text)

        result = self.test_client.delete(f"/file?uri={os.path.abspath(filepath)}")
        self.assertEqual(HTTPStatus.NO_CONTENT, result.status_code)

        response = self.test_client.get("/files/context")
        self.assertEqual(HTTPStatus.OK, response.status_code)
        self.assertNotIn("calculator.yaml", response.text)


class TestAacRestApiDefinitions(TestCase):
    test_client = TestClient(app)

    def test_get_definitions(self):

        filepath = "tests/calc/model/calculator.yaml"
        self.maxDiff = None

        model_path = os.path.abspath(filepath)
        self.assertTrue(os.path.isfile(model_path))
        definitions = parse(model_path)
        definition_model = to_definition_model(definitions[0])

        post_response = self.test_client.post("/definition", data=json.dumps(jsonable_encoder(definition_model)))
        self.assertEqual(HTTPStatus.NO_CONTENT, post_response.status_code)

        response = self.test_client.get("/definitions")
        self.assertEqual(HTTPStatus.OK, response.status_code)
        self.assertIn(filepath, response.text)

    def test_get_definition_by_name(self):
        refresh_available_files_in_workspace()
        filepath = "tests/calc/model/calculator.yaml"

        defs_to_lookup = ["Calculator", "Multiply", "MathLogger", "Add"]

        model_path = os.path.abspath(filepath)
        self.assertTrue(os.path.isfile(model_path))
        definitions = parse(model_path)
        definition_models = []
        for definition in definitions:
            definition_models.append(to_definition_model(definition))

        post_response = self.test_client.post("/definitions", data=json.dumps(jsonable_encoder(definition_models)))
        self.assertEqual(HTTPStatus.NO_CONTENT, post_response.status_code)

        for definition_name in defs_to_lookup:
            response = self.test_client.get(f"/definition?{DEFINITION_FIELD_NAME}={definition_name}")
            self.assertEqual(HTTPStatus.OK, response.status_code)
            self.assertIn(definition_name, response.text)

    def test_get_definition_by_name_not_found(self):
        fake_definition_name = "FakeModel"

        response = self.test_client.get(f"/definition/{fake_definition_name}")
        self.assertEqual(HTTPStatus.NOT_FOUND, response.status_code)

    def test_update_definitions(self):
        refresh_available_files_in_workspace()

        parsed_definition = parse(TEST_MODEL)[0]
        updated_parsed_definition = parse(UPDATED_TEST_MODEL)[0]

        post_response = self.test_client.post("/definition", data=json.dumps(jsonable_encoder(to_definition_model(parsed_definition))))
        self.assertEqual(HTTPStatus.NO_CONTENT, post_response.status_code)

        get_response = self.test_client.get(f"/definition?{DEFINITION_FIELD_NAME}={parsed_definition.name}")
        self.assertEqual(HTTPStatus.OK, get_response.status_code)
        self.assertIn("A TestModel", get_response.text)
        self.assertNotIn("An updated TestModel", get_response.text)

        update_response = self.test_client.put("/definition", data=json.dumps(jsonable_encoder(to_definition_model(updated_parsed_definition))))
        self.assertEqual(HTTPStatus.NO_CONTENT, update_response.status_code)

        get_response = self.test_client.get(f"/definition?{DEFINITION_FIELD_NAME}={parsed_definition.name}")
        self.assertEqual(HTTPStatus.OK, get_response.status_code)
        self.assertIn("An updated TestModel", get_response.text)
        self.assertNotIn("A TestModel", get_response.text)

    def test_remove_definition(self):
        refresh_available_files_in_workspace()
        filepath = "tests/calc/model/calculator.yaml"
        definition_to_be_deleted = "MathLogger"

        model_path = os.path.abspath(filepath)
        self.assertTrue(os.path.isfile(model_path))
        definitions = parse(model_path)
        definition_models = []
        for definition in definitions:
            definition_models.append(to_definition_model(definition))

        post_response = self.test_client.post("/definitions", data=json.dumps(jsonable_encoder(definition_models)))
        self.assertEqual(HTTPStatus.NO_CONTENT, post_response.status_code)

        get_response = self.test_client.get("/definitions")
        self.assertEqual(HTTPStatus.OK, get_response.status_code)
        self.assertIn("A log management service for calculator.", get_response.text)

        self.test_client.delete(f"/definition?{DEFINITION_FIELD_NAME}={definition_to_be_deleted}")

        get_response = self.test_client.get("/definitions")
        self.assertEqual(HTTPStatus.OK, get_response.status_code)
        self.assertNotIn("A log management service for calculator.", get_response.text)

    def test_get_schema_definition(self):
        get_response = self.test_client.get("/context/schema?key=model")
        self.assertEqual(HTTPStatus.OK, get_response.status_code)
        self.assertIn("Model", get_response.text)
        self.assertIn("A definition that represents a system and/or component model.", get_response.text)

    def test_get_root_keys(self):
        refresh_available_files_in_workspace()
        filepath = "tests/calc/model/calculator.yaml"

        model_path = os.path.abspath(filepath)
        self.assertTrue(os.path.isfile(model_path))
        definitions = parse(model_path)
        definition_models = []
        for definition in definitions:
            definition_models.append(to_definition_model(definition))

        post_response = self.test_client.post("/definitions", data=json.dumps(jsonable_encoder(definition_models)))
        self.assertEqual(HTTPStatus.NO_CONTENT, post_response.status_code)

        get_response = self.test_client.get("/context/root_keys")
        self.assertEqual(HTTPStatus.OK, get_response.status_code)
        self.assertIn("model", get_response.text)
        self.assertIn("plugin", get_response.text)
        self.assertIn("req", get_response.text)
        self.assertIn("req_spec", get_response.text)


TEST_MODEL = """
model:
    name: TestModel
    description: A TestModel
"""

UPDATED_TEST_MODEL = """
model:
    name: TestModel
    description: An updated TestModel
"""

BAD_TEST_MODEL = """
model:
    description: A TestModel
"""
