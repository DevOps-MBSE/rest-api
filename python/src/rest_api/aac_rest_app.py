from fastapi import FastAPI, HTTPException, BackgroundTasks, responses, exceptions, Request
from http import HTTPStatus
import logging
from typing import Optional
import os

from aac.context.definition_parser import DefinitionParser
from aac.context.language_context import LanguageContext
from aac.context.language_error import LanguageError
from aac.context.definition import Definition
from aac.in_out.files.aac_file import AaCFile
from aac.in_out.files.find import find_aac_files, is_aac_file
from aac.in_out.paths import sanitize_filesystem_path
from aac.in_out.parser import parse, ParserError
from aac.execute.aac_execution_result import ExecutionStatus
from aac.execute.plugin_runner import AacCommand, AacCommandArgument

from rest_api.models.command_model import (CommandModel, CommandRequestModel, CommandResponseModel, to_command_model)
from rest_api.models.definition_model import DefinitionModel, to_definition_class, to_definition_model
from rest_api.models.file_model import FileModel, FilePathModel, FilePathRenameModel, to_file_model

app = FastAPI()

# Global
AVAILABLE_AAC_FILES: list[AaCFile] = []
ACTIVE_CONTEXT = LanguageContext()
WORKSPACE_DIR: str = os.getcwd()


# File CRUD Operations
def _get_files_in_context() -> list[AaCFile]:
    """
    Returns a list of all files contributing definitions to the active context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext):  The global active language context.

    Returns:
        A list of all files contributing definitions to the active context.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    return list({definition.source for definition in ACTIVE_CONTEXT.get_definitions()})


def _get_file_in_context_by_uri(uri: str) -> Optional[AaCFile]:
    """
    Return the AaCFile object by uri from the context or None if the file isn't in the context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext):  The global active language context.

    Args:
        uri (str): The string uri to search for.

    Returns:
        An optional AaCFile if it's present in the context, otherwise None.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    for definition in ACTIVE_CONTEXT.get_definitions():
        if definition.source.uri == uri:
            return definition.source


def _get_definitions_by_file_uri(file_uri: str) -> list[Definition]:
    """
    Return a subset of definitions that are sourced from the target file URI.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext):  The global active language context.

    Args:
        file_uri (str): The source file URI to filter on.

    Returns:
        A list of definitions belonging to the target file.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()
    definitions = ACTIVE_CONTEXT.get_definitions()
    return [definition for definition in definitions if str(file_uri) == str(definition.source.uri)]


@app.get("/files/context", status_code=HTTPStatus.OK, response_model=list[FileModel])
def get_files_from_context():
    """
    Return a list of file model definitions of files contributing definitions.

    Returns:
        A list of FileModel objects representing files in the active context.
    """
    return [to_file_model(file) for file in _get_files_in_context()]


@app.get("/files/available", status_code=HTTPStatus.OK, response_model=list[FileModel])
def get_available_files(background_tasks: BackgroundTasks):
    """
    Return a list of all files available in the workspace for import into the active context. The list of files returned does not include files already in the context.

    Args:
        background_tasks (BackgroundTasks): A BackgroundTasks object containing all currently active background tasks.

    Returns:
        A list of files available for import into the active context.
    """
    # Update the files via an async function so that any changes to the files shows up, eventually.
    background_tasks.add_task(refresh_available_files_in_workspace)

    #  Having to use a cached response for now as the file-walking makes the response take too long.
    return [to_file_model(file) for file in list(_get_available_files_in_workspace())]


@app.get("/file", status_code=HTTPStatus.OK, response_model=FileModel)
def get_file_by_uri(uri: str):
    """
    Return the target file from the workspace

    Args:
        uri (str): The string uri to search for.

    Returns:
        Target file from the workspace, or HTTPStatus.NOT_FOUND if the file isn't in the context
    """
    file_in_context = _get_file_in_context_by_uri(uri)

    if file_in_context:
        file_model = to_file_model(file_in_context)
        with open(file_in_context.uri) as file:
            file_model.content = file.read()

        return file_model
    else:
        _report_error_response(HTTPStatus.NOT_FOUND, f"File {uri} not found in the context.")


@app.post("/files/import", status_code=HTTPStatus.NO_CONTENT)
def import_files_to_context(file_models: list[FilePathModel]) -> None:
    """
    Import the list of files into the context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext):  The global active language context.

    Args:
        file_models (list[FilePathModel]): List of file models for import.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    files_to_import = set([str(model.uri) for model in file_models])
    valid_aac_files = set(filter(is_aac_file, files_to_import))
    invalid_files = files_to_import.difference(valid_aac_files)
    if len(invalid_files) > 0:
        _report_error_response(
            HTTPStatus.BAD_REQUEST,
            f"Invalid files were asked to imported. Invalid files: {invalid_files}.",
        )
    else:
        try:
            new_file_definitions = [parse(file) for file in valid_aac_files]
        except ParserError as error:
            raise ParserError(error.source, error.errors) from None

        parser = DefinitionParser()
        for file in new_file_definitions:
            parser.load_definitions(ACTIVE_CONTEXT, file)


@app.put("/file", status_code=HTTPStatus.NO_CONTENT)
def rename_file_uri(rename_request: FilePathRenameModel) -> None:
    """
    Update a file's uri. (Rename file).

    Args:
        rename_request (FilePathRenameModel): A RestAPI model for renaming a file.
    """
    current_file_path = sanitize_filesystem_path(str(rename_request.current_file_uri))
    new_file_path = sanitize_filesystem_path(rename_request.new_file_uri)

    file_in_context = _get_file_in_context_by_uri(current_file_path)

    if not _is_file_path_in_working_directory(new_file_path):
        _report_error_response(
            HTTPStatus.BAD_REQUEST,
            f"Files can only be renamed to a uri inside of the working directory: {WORKSPACE_DIR}.",
        )

    if file_in_context:
        os.rename(current_file_path, new_file_path)
        definitions_to_update = _get_definitions_by_file_uri(current_file_path)
        for definition in definitions_to_update:
            definition.source.uri = new_file_path

    else:
        _report_error_response(HTTPStatus.NOT_FOUND, f"File {current_file_path} not found in the context.")


@app.delete("/file", status_code=HTTPStatus.NO_CONTENT)
def remove_file_by_uri(uri: str) -> None:
    """
    Remove the requested file and it's associated definitions from the active context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Args:
        uri (str): uri (str): The string uri of the files to be removed.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    file_in_context = _get_file_in_context_by_uri(uri)
    if not file_in_context:
        _report_error_response(HTTPStatus.NOT_FOUND, f"File {uri} not found in the context.")

    definitions_to_remove = []
    discovered_definitions = _get_definitions_by_file_uri(uri)
    definitions_to_remove.extend(discovered_definitions)

    if len(discovered_definitions) == 0:
        _report_error_response(
            HTTPStatus.NOT_FOUND,
            f"No definition(s) from {uri} were found in the context; Will not remove any definitions or files from the context.",
        )

    ACTIVE_CONTEXT.remove_definitions(definitions_to_remove)


# Definition CRUD Operations
@app.get("/definitions", status_code=HTTPStatus.OK, response_model=list[DefinitionModel])
def get_definitions() -> list[DefinitionModel]:
    """
    Return a list of the definitions in the active context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Returns:
        A list of definitions represented as DefinitionModel objects.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    definition_models = [to_definition_model(definition) for definition in ACTIVE_CONTEXT.get_definitions()]
    return definition_models


@app.get("/definition", status_code=HTTPStatus.OK, response_model=list[DefinitionModel])
def get_definition_by_name(name: str) -> list[DefinitionModel]:
    """
    Returns a definition from active context by name, or HTTPStatus.NOT_FOUND not found if the definition doesn't exist.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Args:
        name (str): Name of the definition to be returned

    Returns:
        Returns the definitions with the given name as a list containing DefinitionModel objects.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    definitions = ACTIVE_CONTEXT.get_definitions_by_name(name)

    if not definitions:
        _report_error_response(HTTPStatus.NOT_FOUND, f"Definition {name} not found in the context.")
    else:
        definition_models = [to_definition_model(definition) for definition in definitions]

        return definition_models


@app.post("/definition", status_code=HTTPStatus.NO_CONTENT)
def add_definition(definition_model: DefinitionModel) -> None:
    """
    Add the definition to the active context. If the definition's source file doesn't exist, a new one will be created.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Args:
        definition_model (DefinitionModel): The definition model in request body.

    Returns:
        204 HTTPStatus.NO_CONTENT if successful.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    definition_source_uri = sanitize_filesystem_path(definition_model.source_uri)

    if not _is_file_path_in_working_directory(definition_source_uri):
        _report_error_response(
            HTTPStatus.BAD_REQUEST,
            f"Definition can't be added to a file {definition_source_uri} which is outside of the working directory: {WORKSPACE_DIR}.",
        )
    definition_to_add = to_definition_class(definition_model)
    existing_definitions = _get_definitions_by_file_uri(definition_source_uri)

    is_user_editable = True
    if len(existing_definitions) > 0:
        is_user_editable = existing_definitions[0].source.is_user_editable

    if not is_user_editable:
        _report_error_response(
            HTTPStatus.BAD_REQUEST,
            f"File {definition_source_uri} can't be edited by users.",
        )

    parser = DefinitionParser()
    parser.load_definitions(ACTIVE_CONTEXT, [definition_to_add])


@app.post("/definitions", status_code=HTTPStatus.NO_CONTENT)
def add_definitions(definition_models: list[DefinitionModel]) -> None:
    """
    Add the definitions to the active context. If the definition's source file doesn't exist, a new one will be created.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Args:
        definition_models (list[DefinitionModel]): The list of definition models in request body.

    Returns:
        204 HTTPStatus.NO_CONTENT if successful.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    definitions_to_add = []
    for definition_model in definition_models:
        definition_source_uri = sanitize_filesystem_path(definition_model.source_uri)

        if not _is_file_path_in_working_directory(definition_source_uri):
            _report_error_response(
                HTTPStatus.BAD_REQUEST,
                f"Definition can't be added to a file {definition_source_uri} which is outside of the working directory: {WORKSPACE_DIR}.",
            )
        definitions_to_add.append(to_definition_class(definition_model))
        existing_definitions = _get_definitions_by_file_uri(definition_source_uri)

        is_user_editable = True
        if len(existing_definitions) > 0:
            is_user_editable = existing_definitions[0].source.is_user_editable

        if not is_user_editable:
            _report_error_response(
                HTTPStatus.BAD_REQUEST,
                f"File {definition_source_uri} can't be edited by users.",
            )

    parser = DefinitionParser()
    parser.load_definitions(ACTIVE_CONTEXT, definitions_to_add)


@app.put("/definition", status_code=HTTPStatus.NO_CONTENT)
def update_definition(definition_model: DefinitionModel) -> None:
    """
    Update the request body definitions in the active context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Args:
        definition_model (DefinitionModel): The definition to be updated.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    definitions_to_update = ACTIVE_CONTEXT.get_definitions_by_name(definition_model.name)

    if definitions_to_update:
        updated_definition = to_definition_class(definition_model)
        for definition in definitions_to_update:
            if definition.name == updated_definition.name:
                updated_definition.uid = definition.uid
        ACTIVE_CONTEXT.remove_definitions(definitions_to_update)
        parser = DefinitionParser()
        parser.load_definitions(ACTIVE_CONTEXT, [updated_definition])

    else:
        _report_error_response(
            HTTPStatus.NOT_FOUND,
            f"Definition(s) {definition_model.name} not found in the context; failed to update definitions.",
        )


@app.delete("/definition", status_code=HTTPStatus.NO_CONTENT)
def remove_definition_by_name(name: str) -> None:
    """
    Remove the definition via name from the active context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Args:
        name (str): Name of the definition to be removed.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    definitions_to_remove = ACTIVE_CONTEXT.get_definitions_by_name(name)

    if definitions_to_remove:
        ACTIVE_CONTEXT.remove_definitions(definitions_to_remove)
    else:
        _report_error_response(
            HTTPStatus.NOT_FOUND,
            f"Definition {name} not found in the context; failed to delete definitions.",
        )


# Language Context Support

@app.get("/context/schema", status_code=HTTPStatus.OK, response_model=DefinitionModel)
def get_root_key_schema(key: str) -> DefinitionModel:
    """
    Returns the YAML schema for the given root key, or HTTPStatus.NOT_FOUND not found if the key doesn't exist.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Args:
        key (str): The key of the root schema to be returned.

    Returns:
        Schema_Model containing the root schema with the given key.
        200 HTTPStatus.OK if successful.
        404 HTTPStatus.NOT_FOUND if the key doesn't exist.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    root_definitions = [definition for definition in ACTIVE_CONTEXT.get_definitions() if definition.get_root_key()]
    matching_definitions = [definition for definition in root_definitions if definition.name == key.capitalize()]

    if not matching_definitions:
        _report_error_response(HTTPStatus.NOT_FOUND, f"No root key found called {key}.")
    else:
        schema_definition = matching_definitions[0]

        if not schema_definition:
            _report_error_response(HTTPStatus.NOT_FOUND, f"Unable to get the schema definition {schema_definition.name}.")
        else:
            schema_model = to_definition_model(schema_definition)
            return schema_model


@app.get("/context/root_keys", status_code=HTTPStatus.OK, response_model=list[str])
def get_language_context_root_keys() -> list[str]:
    """
    Returns a list of root keys from the active context.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Returns:
        A list containing all root keys in the active context.
        200 HTTPStatus.OK
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    return [str(definition.get_root_key()) for definition in ACTIVE_CONTEXT.definitions if definition.get_root_key()]


# AaC Plugin Commands


@app.get("/commands", status_code=HTTPStatus.OK, response_model=list[CommandModel])
def get_aac_commands() -> list[CommandModel]:
    """
    Return a list of all available plugin commands.

    Returns:
        A list of CommandModel objects
    """
    aac_and_plugin_commands = _get_rest_api_compatible_commands()
    return [to_command_model(aac_and_plugin_commands[command]) for command in aac_and_plugin_commands]


@app.post("/command", status_code=HTTPStatus.OK, response_model=CommandResponseModel)
def execute_aac_command(command_request: CommandRequestModel):
    """
    Execute the command and return the result.

    Args:
        command_request (CommandRequestModel): The AaC command to be executed.

    Returns:
        A CommandResponseModel object containing the result of the command execution.
    """
    aac_commands_by_name = _get_rest_api_compatible_commands()
    aac_command = aac_commands_by_name.get(command_request.name)

    if aac_command is not None:
        aac_command_argument_names = [arg.name for arg in aac_command.arguments]
        arguments = [arg for arg in command_request.arguments if arg not in aac_command_argument_names]

        try:
            result = aac_command.callback(*(arguments or []))
            success = result.status_code == ExecutionStatus.SUCCESS
            result_message = f"{result.plugin_name}: {result.status_code.name.lower()}\n\n{result.get_messages_as_string()}"
        except Exception as error:
            success = False
            result_message = f"{result.plugin_name}: failure\n\n{error}"
        finally:
            return CommandResponseModel(command_name=aac_command.name, result_message=result_message, success=success)
    else:
        _report_error_response(
            HTTPStatus.NOT_FOUND,
            f"Command name {command_request.name} not found in the list of available commands: {list(aac_commands_by_name.keys())}.",
        )


def _get_available_files_in_workspace() -> set[AaCFile]:
    """
    Get the available AaC files in the workspace sans files already in the context.

    Returns:
        A set containing available AaC files in workspace.
    """
    aac_files_in_context = set(_get_files_in_context())
    aac_files_in_workspace = set(find_aac_files(WORKSPACE_DIR))

    return aac_files_in_workspace.difference(aac_files_in_context)


async def refresh_available_files_in_workspace() -> None:
    """
    Used to refresh the available files. Used in async since it takes too long for being used in request-response flow.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The active Language Context.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    AVAILABLE_AAC_FILES = list(_get_available_files_in_workspace())

    # Update the active context with any missing files
    files_in_context = {file.uri for file in _get_files_in_context()}
    available_files = {file.uri for file in AVAILABLE_AAC_FILES}
    missing_files = available_files.difference(files_in_context)
    try:
        definition_lists_from_missing_files = [parse(file_uri) for file_uri in missing_files]
    except ParserError as error:
        raise ParserError(error.source, error.errors) from None
    else:
        definitions_to_add = {definition.name: definition for definition_list in definition_lists_from_missing_files for definition in definition_list}
        parser = DefinitionParser()
        parser.load_definitions(ACTIVE_CONTEXT, list(definitions_to_add.values()))


def _report_error_response(code: HTTPStatus, error: str):
    """
    Accepts an error string and raises an HTTPException error.

    Args:
        code (HTTPStatus): The HTTP Status code
        error (str): An error message\

    Raises:
        HTTPException.
    """
    logging.error(error)
    raise HTTPException(
        status_code=code,
        detail=error,
    )


def _is_file_path_in_working_directory(file_path: str) -> bool:
    """
    Checks if the file path exists in the working directory.

    Args:
        file_path (str): Path to the file.

    Returns:
        A bool value of True if the file path exists in the working directory.
    """
    return str(file_path).startswith(WORKSPACE_DIR)


def _get_rest_api_compatible_commands() -> dict[str, AacCommand]:
    """
    Filter out plugin commands that aren't compatible with the rest-api command. These commands are long-running commands that don't allow for a timely rest response.

    Global Args:
        ACTIVE_CONTEXT (LanguageContext): The global active language context.

    Returns:
        A dictionary containing compatible commands, with the commands name as the key.
    """
    global ACTIVE_CONTEXT
    if not ACTIVE_CONTEXT:
        ACTIVE_CONTEXT = LanguageContext()

    long_running_commands = ["rest-api", "start-lsp-io", "start-lsp-tcp"]

    result: list[AacCommand] = []
    for runner in ACTIVE_CONTEXT.get_plugin_runners():
        definition = runner.plugin_definition
        for plugin_command in definition.instance.commands:
            if plugin_command not in long_running_commands:
                arguments: list[AacCommandArgument] = []
                for input in plugin_command.input:
                    arguments.append(
                        AacCommandArgument(
                            input.name,
                            input.description,
                            ACTIVE_CONTEXT.get_python_type_from_primitive(input.type),
                            input.default,
                        )
                    )
                result.append(
                    AacCommand(
                        plugin_command.name,
                        plugin_command.help_text,
                        runner.command_to_callback[plugin_command.name],
                        arguments,
                    )
                )

    return {command.name: command for command in result}


# Error Handlers


@app.exception_handler(LanguageError)
async def language_error_exception_handler(request, exc):
    """
    If a `LanguageError` exception is encountered, then return a 400 BAD Request with the exception's message.

    Args:
        request (Request): The encountered request.
        exc (LanguageError): The encountered LanguageError exception.

    Returns:
        The exception message response.
    """
    return responses.PlainTextResponse(str(exc), status_code=400)


@app.exception_handler(exceptions.RequestValidationError)
async def validation_exception_handler(request: Request, exc: exceptions.RequestValidationError):
    """
    If a `exceptions.RequestValidationError` exception is encountered, then return a 422 UNPROCESSABLE ENTITY with the exception's message.

    Args:
        request (Request): The encountered request.
        exc (RequestValidationError): The encountered RequestValidationError Exception.
    """
    _report_error_response(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
