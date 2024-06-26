"""The AaC REST API plugin implementation module."""

# NOTE: It is safe to edit this file.
# This file is only initially generated by aac gen-plugin, and it won't be overwritten if the file already exists.

# There may be some unused imports depending on the definition of the plugin, be sure to remove unused imports.
import asyncio
import json
import logging
import os
import uvicorn
from fastapi.openapi.utils import get_openapi
from typing import Any

from aac.context.definition import Definition
from aac.context.language_context import LanguageContext
from aac.context.source_location import SourceLocation
from aac.execute.aac_execution_result import (
    ExecutionResult,
    ExecutionStatus,
    ExecutionMessage,
    MessageLevel,
)
from aac.in_out.files.aac_file import AaCFile

from rest_api.aac_rest_app import app, refresh_available_files_in_workspace

plugin_name = "REST API"


def rest_api(host: str, port: int) -> ExecutionResult:
    """
    Business logic for allowing rest-api command to perform Start a RESTful interface for interacting with and managing AaC.

    Args:
         host (str): Set the hostname of the service. Useful for operating behind proxies.
         port (int): The port to which the RESTful service will be bound.

    Returns:
         The results of the execution of the rest-api command.
    """

    status = ExecutionStatus.GENERAL_FAILURE

    if isinstance(port, str):
        port = int(port)

    msg_str = _start_restful_service(host, port)

    status = ExecutionStatus.SUCCESS
    messages: list[ExecutionMessage] = []
    msg = ExecutionMessage(
        msg_str,
        MessageLevel.INFO,
        None,
        None,
    )
    messages.append(msg)

    return ExecutionResult(plugin_name, "rest-api", status, messages)

def _start_restful_service(host: str, port: int) -> str:
    """
    Starts the RESTful interface service.

    Args:
        host (str): The hostname of the service.
        port (int): The port to which the RESTful service will be bound.

    Returns:
        A success message.
    """
    asyncio.run(refresh_available_files_in_workspace(LanguageContext()))
    logging.info(f"Starting REST API in {os.getcwd()}.")
    uvicorn.run(app, host=host, port=port)
    return "Successfully started the RESTful API."


def gen_openapi_spec(output_directory: str) -> ExecutionResult:
    """
    Business logic for allowing gen-openapi-spec command to perform Write the OpenAPI schema to a JSON file.

    Args:
         output_directory (str): The output directory in which to write the AaC OpenAPI JSON file.

    Returns:
         The results of the execution of the gen-openapi-spec command.
    """

    status = ExecutionStatus.GENERAL_FAILURE

    msg_str = _write_openapi_spec_to_file(output_directory)

    status = ExecutionStatus.SUCCESS
    messages: list[ExecutionMessage] = []
    msg = ExecutionMessage(
        msg_str,
        MessageLevel.INFO,
        None,
        None,
    )
    messages.append(msg)

    return ExecutionResult(plugin_name, "gen-openapi-spec", status, messages)


def _write_openapi_spec_to_file(output_directory: str) -> str:
    """
    Writes the open api spec to a json file.

    Args:
        output_directory (str): The directory in which the json file will be created.

    Returns:
        A success message.
    """
    full_file_path = os.path.join(output_directory, "AaC_OpenAPI_Schema.json")

    with open(full_file_path, "w") as output_file:
        json.dump(
            get_openapi(
                title=app.title,
                version=app.version,
                openapi_version=app.openapi_version,
                description=app.description,
                routes=app.routes,
            ),
            output_file,
        )

    return f"Successfully wrote the OpenAPI spec to {full_file_path}."
