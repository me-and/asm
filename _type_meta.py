import dataclasses
from typing import (
        Annotated,
        Any,
        Generic,
        Iterable,
        Literal,
        Optional,
        TypeAlias,
        TypeVar,
        Union,
        get_args,
        get_origin,
        )
from enum import IntEnum
from datetime import datetime

from pydantic import (
        AfterValidator,
        AwareDatetime,
        BaseModel as _BaseModel,
        ConfigDict,
        GetCoreSchemaHandler,
        GetJsonSchemaHandler,
        SerializerFunctionWrapHandler,
        TypeAdapter,
        ValidationInfo,
        )
from pydantic.json_schema import JsonSchemaValue
from pydantic.alias_generators import to_camel
from pydantic_core import core_schema

T = TypeVar('T')

# Adapted from pydantic_core._pydantic_core
IncEx: TypeAlias = Union[set[int], set[str],
                         dict[int, 'IncEx'], dict[str, 'IncEx'],
                         None,
                         ]


def add_condition_to_json_schema(schema: JsonSchemaValue,
                                 condition: JsonSchemaValue,
                                 ) -> JsonSchemaValue:
    if schema.keys() & condition.keys():
        # There's overlap between the keys, so we can't just merge the schemas.
        # Instead, create or extend an allOf element.
        try:
            all_of = schema['allOf']
        except KeyError:
            all_of = schema['allOf'] = []
        all_of.append(condition)
    else:
        # There's no overlap between the keys, so we can just merge the two
        # schemas together.
        schema |= condition
    return schema


class BaseModel(_BaseModel):

    model_config = ConfigDict(extra='forbid',
                              alias_generator=to_camel,
                              )

    def model_dump(self, *,
                   mode: str | Literal["json", "python"] = "python",
                   include: IncEx = None,
                   exclude: IncEx = None,
                   by_alias: bool = True,  # Different to superclass
                   exclude_unset: bool = False,
                   exclude_defaults: bool = True,  # Different to superclass
                   exclude_none: bool = False,
                   round_trip: bool = False,
                   warnings: bool = True,
                   ) -> dict[str, Any]:
        return super().model_dump(mode=mode,
                                  include=include,
                                  exclude=exclude,
                                  by_alias=by_alias,
                                  exclude_unset=exclude_unset,
                                  exclude_defaults=exclude_defaults,
                                  exclude_none=exclude_none,
                                  round_trip=round_trip,
                                  warnings=warnings,
                                  )

    def model_dump_json(
            self, *,
            indent: int | None = None,
            include: IncEx = None,
            exclude: IncEx = None,
            by_alias: bool = True,  # Different to superclass
            exclude_unset: bool = False,
            exclude_defaults: bool = True,  # Different to superclass
            exclude_none: bool = False,
            round_trip: bool = False,
            warnings: bool = True,
            ) -> str:
        return super().model_dump_json(indent=indent,
                                       include=include,
                                       exclude=exclude,
                                       by_alias=by_alias,
                                       exclude_unset=exclude_unset,
                                       exclude_defaults=exclude_defaults,
                                       exclude_none=exclude_none,
                                       round_trip=round_trip,
                                       warnings=warnings,
                                       )


class IntEnumSchema(IntEnum):
    def __str__(self) -> str:
        return self.name

    @classmethod
    def __get_pydantic_core_schema__(cls,
                                     source_type: Any,
                                     handler: GetCoreSchemaHandler,
                                     ) -> core_schema.CoreSchema:
        int_schema = core_schema.no_info_after_validator_function(
                cls,
                core_schema.literal_schema([f.value for f in cls]),
                )
        str_schema = core_schema.no_info_after_validator_function(
                cls.__getitem__,
                core_schema.literal_schema([f.name for f in cls]),
                )
        full_schema = core_schema.union_schema([str_schema, int_schema])

        return core_schema.json_or_python_schema(
            json_schema=full_schema,
            python_schema=core_schema.union_schema([
                core_schema.is_instance_schema(cls),
                full_schema,
                ]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=str_schema),
            )

    @classmethod
    def get_all_names(cls) -> list[str]:
        return [v.name for v in cls]


class SingletonToList(list[T]):
    def __init__(self, __v: Optional[T | Iterable[T]] = None) -> None:
        # This function should probably never be called, but this _is_ a
        # subclass of list, so to play nicely, we'll implement an __init__ that
        # can handle a singleton as well as a full item, by making use of the
        # Pydantic schemas.
        if __v is None:
            super().__init__()
        else:
            ta = TypeAdapter(type(self))
            super().__init__(ta.validate_python(__v))

    @classmethod
    def __get_pydantic_core_schema__(cls,
                                     source_type: Any,
                                     handler: GetCoreSchemaHandler,
                                     ) -> core_schema.CoreSchema:

        def singleton_to_list(item: T) -> list[T]:
            return [item]

        def serialize(l: list[T],
                      handler: SerializerFunctionWrapHandler
                      ) -> Any:
            if len(l) == 1:
                return handler(l[0])
            return handler(l)

        item_type: Any
        if get_origin(source_type) is None:
            item_type = Any
        else:
            item_type = get_args(source_type)[0]

        item_schema = handler.generate_schema(item_type)
        list_schema = core_schema.list_schema(items_schema=item_schema)

        listmaking_schema = core_schema.no_info_after_validator_function(
                singleton_to_list, item_schema)

        return core_schema.union_schema(
                [list_schema, listmaking_schema],
                serialization=core_schema.wrap_serializer_function_ser_schema(
                    serialize),
                )


@dataclasses.dataclass(frozen=True, slots=True)
class ForbidValue(Generic[T]):
    value: T

    def _check_value(self, value: T, info: ValidationInfo) -> T:
        if self.value == value:
            field = info.field_name or 'value'
            raise ValueError(f'{field} cannot be {value!r}')
        return value

    def __get_pydantic_json_schema__(self,
                                     core_schema: core_schema.CoreSchema,
                                     handler: GetJsonSchemaHandler,
                                     ) -> JsonSchemaValue:
        json_schema = handler(core_schema)
        json_schema = handler.resolve_ref_schema(json_schema)
        json_schema = add_condition_to_json_schema(
                json_schema, {'not': {'const': self.value}})
        return json_schema

    def __get_pydantic_core_schema__(self,
                                     source_type: Any,
                                     handler: GetCoreSchemaHandler,
                                     ) -> core_schema.CoreSchema:
        return core_schema.with_info_after_validator_function(
                self._check_value,
                schema=handler(source_type),
                field_name=handler.field_name,
                )


NotZero = Annotated[T, ForbidValue(0)]


def make_datetime_aware(dt: datetime) -> AwareDatetime:
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt

DatetimeToAware = Annotated[datetime, AfterValidator(make_datetime_aware)]
