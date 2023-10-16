from typing import (
        Annotated,
        ClassVar,
        Generic,
        Literal,
        Self,
        TypeVar,
        cast,
        get_args,
        )

from pydantic import (
        GetCoreSchemaHandler,
        GetJsonSchemaHandler,
        SerializerFunctionWrapHandler,
        )
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema
import dateutil.relativedelta as relativedelta
from dateutil._common import weekday  # No better option I can see :(

from _type_meta import IntEnumSchema

DayName = Literal['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']

class WeekdayName(IntEnumSchema):
    MO = relativedelta.MO.weekday
    TU = relativedelta.TU.weekday
    WE = relativedelta.WE.weekday
    TH = relativedelta.TH.weekday
    FR = relativedelta.FR.weekday
    SA = relativedelta.SA.weekday
    SU = relativedelta.SU.weekday

    @classmethod
    def _from_weekday(cls, wd: weekday) -> Self:
        return cls(wd.weekday)


assert set(get_args(DayName)) == set(WeekdayName.get_all_names())

WDT = TypeVar('WDT', bound=weekday)

# TODO: A better schema here for serializing might be serializing to the string
# for the simple case, or to a singleton object with the string as the key and
# the offset as the value for the complex case.  In particular this means when
# serializing to YAML, it'll serialize as either 'MO' or 'MO: 3', which just
# seems nicer.
class _dateutilWeekdayOffsetAnnotation(Generic[WDT]):
    _pattern: ClassVar[str] = (r'^('
                               + '|'.join(d.name for d in WeekdayName)
                               + r')\([+-]?[0-9]+\)$'
                               )

    @staticmethod
    def _serialize(wd: weekday,
                   handler: SerializerFunctionWrapHandler,
                   ) -> DayName | dict[DayName, int]:
        name = cast(DayName, str(WeekdayName._from_weekday))
        if wd.n:
            return handler({name: wd.n})
        return handler(name)

    @classmethod
    def __get_pydantic_core_schema__(cls,
                                     source_type: type[WDT],
                                     handler: GetCoreSchemaHandler,
                                     ) -> core_schema.CoreSchema:
        day_name_schema = core_schema.chain_schema([
                core_schema.str_schema(to_upper=True),
                core_schema.literal_schema([d.name for d in WeekdayName]),
                ])

        # A string that's just a day name
        simple_schema = core_schema.no_info_after_validator_function(
            relativedelta.__getattribute__,
            day_name_schema,
            )

        def validate_dict(d: dict[DayName, int]) -> WDT:
            name, num = d.popitem()
            return source_type(int(WeekdayName[name]), num)

        # A day name as a key, an integer as a value.
        dict_schema = core_schema.no_info_after_validator_function(
            validate_dict,
            core_schema.dict_schema(
                keys_schema=day_name_schema,
                values_schema=core_schema.int_schema(),
                min_length=1,
                max_length=1,
                )
            )

        full_schema = core_schema.union_schema([simple_schema, dict_schema])
        return core_schema.json_or_python_schema(
                json_schema=full_schema,
                python_schema=core_schema.union_schema([
                    core_schema.is_instance_schema(cls),
                    full_schema,
                    ]),
                serialization=core_schema.wrap_serializer_function_ser_schema(
                    cls._serialize),
                )

    @classmethod
    def __get_pydantic_json_schema__(cls,
                                     core_schema: core_schema.CoreSchema,
                                     handler: GetJsonSchemaHandler,
                                     ) -> JsonSchemaValue:

        name_schema = {'enum': [d.name for d in WeekdayName]}
        dict_schema = {'type': 'object',
                       'additionalProperties': {'type': 'integer'},
                       'propertyNames': name_schema,
                       'minProperties': 1,
                       'maxProperties': 1,
                       }
        return {'anyOf': [name_schema, dict_schema]}


WeekdayOffsetT = Annotated[WDT, _dateutilWeekdayOffsetAnnotation]
