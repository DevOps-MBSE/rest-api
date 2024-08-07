"""Pydantic Version of the AaC Definition Class."""
from uuid import UUID
from typing import Optional
from pydantic import BaseModel

from aac.in_out.files.aac_file import AaCFile
from aac.context.definition import Definition


class DefinitionModel(BaseModel):
    """REST API Model for the Definition class."""
    uid: Optional[UUID]
    name: str
    package: str
    is_user_editable: Optional[bool]
    content: Optional[str]
    source_uri: str
    structure: dict


def to_definition_model(definition: Definition) -> DefinitionModel:
    """Return a DefinitionModel representation from a Definition object."""
    return DefinitionModel(
        uid=definition.uid,
        name=definition.name,
        package=definition.package,
        is_user_editable=definition.source.is_user_editable,
        content=definition.content,
        source_uri=definition.source.uri,
        structure=definition.structure,
    )


def to_definition_class(definition_model: DefinitionModel) -> Definition:
    """Return a Definition object from a DefinitionModel object."""
    source = AaCFile(definition_model.source_uri, True, False)
    definition = Definition(name=definition_model.name, package=definition_model.package, content="", source=source, structure=definition_model.structure)
    definition.uid = definition_model.uid
    definition.content = definition.to_yaml()
    return definition
