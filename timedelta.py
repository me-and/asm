from datetime import timedelta
from typing import (
        TYPE_CHECKING,
        Annotated,
        Any,
        ClassVar,
        Iterator,
        Mapping,
        Optional,
        Self,
        get_args,
        get_origin,
        )
from itertools import chain
from inspect import get_annotations, signature
import re

from pydantic import (
        Field,
        GetCoreSchemaHandler,
        SerializerFunctionWrapHandler,
        TypeAdapter,
        )
from pydantic.fields import FieldInfo
from pydantic_core import core_schema
import dateutil.relativedelta as relativedelta
from annotated_types import Ge, Le

from weekday import WeekdayOffsetT

# This is terrible, but I can't come up with a better alternative that doesn't
# require writing my own stubs for dateutil.
if TYPE_CHECKING:
    WeekdayOffset = WeekdayOffsetT[relativedelta._Weekday]
else:
    WeekdayOffset = WeekdayOffsetT[relativedelta.weekday]


class RelativeTime(relativedelta.relativedelta,
                    Mapping[str, int | WeekdayOffset | None]):
    _re: ClassVar[re.Pattern[str]] = re.compile(
        r'^P(?:'
            r'(?:(?P<years>[+-]?\d+)Y)?'
            r'(?:(?P<months>[+-]?\d+)M)?'
            r'(?:(?P<days>[+-]?\d+)D)?'
            r'(?:T'
                r'(?:(?P<hours>[+-]?\d+)H)?'
                r'(?:(?P<minutes>[+-]?\d+)M)?'
                r'(?:(?P<seconds>[+-]?\d+)S)?'
            r')?'
            r'|(?P<weeks>\d+)W'
        r')$'
        )

    def __init__(
            self, *,
            years: int = 0,
            months: int = 0,
            days: int = 0,
            leapdays: int = 0,
            weeks: Annotated[int, Field(exclude=True)] = 0,
            hours: int = 0,
            minutes: int = 0,
            seconds: int = 0,
            year: Optional[Annotated[int, Ge(-9999), Le(9999)]] = None,
            month: Optional[Annotated[int, Ge(1), Le(12)]] = None,
            day: Optional[Annotated[int, Ge(1), Le(31)]] = None,
            weekday: Optional[WeekdayOffset] = None,
            yearday: Annotated[Optional[int], Field(exclude=True)] = None,
            nlyearday: Annotated[Optional[int], Field(exclude=True)] = None,
            hour: Optional[Annotated[int, Ge(0), Le(23)]] = None,
            minute: Optional[Annotated[int, Ge(0), Le(59)]] = None,
            second: Optional[Annotated[int, Ge(0), Le(59)]] = None,
            ) -> None:
        super().__init__(years=years,
                         months=months,
                         days=days,
                         leapdays=leapdays,
                         weeks=weeks,
                         hours=hours,
                         minutes=minutes,
                         seconds=seconds,
                         year=year,
                         month=month,
                         day=day,
                         weekday=weekday,
                         yearday=yearday,
                         nlyearday=nlyearday,
                         hour=hour,
                         minute=minute,
                         second=second,
                         )

    def __getitem__(self, key: str) -> int | WeekdayOffset | None:
        if key in self:
            return getattr(self, key)
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if key == 'return':
            return False

        try:
            type_info = get_annotations(RelativeTime.__init__)[key]  # type: ignore[index]
        except KeyError:
            return False

        if get_origin(type_info) is Annotated:
            try:
                field_info = next(a for a in get_args(type_info)
                                  if isinstance(a, FieldInfo))
            except StopIteration:
                return True
            else:
                return not field_info.exclude
        else:
            return True

    def __iter__(self) -> Iterator[str]:
        for key in get_annotations(RelativeTime.__init__):
            if key in self:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in iter(self))

    def _to_dict(self) -> dict[str, int | WeekdayOffset]:
        d: dict[str, int | WeekdayOffset] = {}
        for key in self:
            value = getattr(self, key)  # Skip redundant checks in __getitem__
            if value != RelativeTime.__init__.__kwdefaults__[key]:
                d[key] = value
        return d

    @classmethod
    def from_str(cls, s: str) -> Self:
        m = cls._re.match(s)
        if m is None:
            raise ValueError(f'Could not validate {s!r} as a duration')
        args = dict((k, int(v)) for k, v in m.groupdict().items()
                    if v is not None)
        return cls(**args)  # type: ignore[arg-type]

    def __str__(self) -> str:
        # This is only possible for simple cases.
        if self.leapdays != 0:
            raise ValueError(f'Cannot convert {self.__class__.__name__} '
                             'with leapdays to string')
        for attr in ('year', 'month', 'day', 'weekday',
                     'hour', 'minute', 'second'):
            if self.__getattribute__(attr) is not None:
                raise ValueError(f'Cannot convert {self.__class__.__name__} '
                                 f'with {attr} to string')

        d_parts: list[str] = []
        t_parts: list[str] = []
        if self.years:
            d_parts.extend((str(self.years), 'Y'))
        if self.months:
            d_parts.extend((str(self.months), 'M'))
        if self.days:
            d_parts.extend((str(self.days), 'D'))
        if self.hours:
            t_parts.extend((str(self.hours), 'H'))
        if self.minutes:
            t_parts.extend((str(self.minutes), 'M'))
        if self.seconds:
            t_parts.extend((str(self.seconds), 'S'))

        if t_parts:
            return ''.join(chain(('P',), d_parts, ('T',), t_parts))
        if d_parts:
            return ''.join(chain(('P',), d_parts))
        return 'PT0S'

    def _to_timedelta(self) -> timedelta:
        # This is only possible for simple cases.
        for attr in ('years', 'months', 'leapdays'):
            if getattr(self, attr) != 0:
                raise ValueError(f'Cannot convert {self.__class__.__name__} '
                                 f'with {attr} to timedelta')
        for attr in ('year', 'month', 'day', 'weekday',
                     'hour', 'minute', 'second'):
            if getattr(self, attr) is not None:
                raise ValueError(f'Cannot convert {self.__class__.__name__} '
                                 f'with {attr} to timedelta')
        return timedelta(days=self.days,
                         hours=self.hours,
                         minutes=self.minutes,
                         seconds=self.seconds,
                         )

    def _serialize(self, handler: SerializerFunctionWrapHandler) -> Any:
        v: timedelta | dict[str, int | WeekdayOffset]
        try:
            v = self._to_timedelta()
        except ValueError:
            v = self._to_dict()
        return handler(v)

    @classmethod
    def __get_pydantic_core_schema__(cls,
                                     source_type: Any,
                                     handler: GetCoreSchemaHandler,
                                     ) -> core_schema.CoreSchema:
        # If there's a string, let's use that.
        str_schema = core_schema.no_info_after_validator_function(
                cls.from_str, core_schema.str_schema())

        # Seems like all this shouldn't be necessary, but just trying to get a
        # handler for the class itself gets recursive, and I've not come up
        # with a quicker way of setting things up that doesn't require
        # repeating everything in the __init__ function's type signature.
        sig = signature(cls.__init__)
        param_schemas: list[core_schema.ArgumentsParameter] = []
        ser_param_schemas: dict[str, core_schema.TypedDictField] = {}
        for name in sig.parameters:
            if name == 'self':
                continue
            param = sig.parameters[name]
            field = FieldInfo.from_annotated_attribute(param.annotation,
                                                       param.default)
            schema = core_schema.with_default_schema(
                    handler.generate_schema(param.annotation),
                    default=param.default,
                    )
            param_schemas.append(core_schema.arguments_parameter(
                    name, schema, mode='keyword_only'))
            if not field.exclude:
                ser_param_schemas[name] = core_schema.typed_dict_field(schema)

        class_call_schema = core_schema.call_schema(
                arguments=core_schema.arguments_schema(param_schemas),
                function=cls,
                )

        full_schema = core_schema.union_schema([str_schema, class_call_schema])

        return core_schema.json_or_python_schema(
                json_schema=full_schema,
                python_schema=core_schema.union_schema(
                    [core_schema.is_instance_schema(cls), full_schema]),
                serialization=core_schema.wrap_serializer_function_ser_schema(
                    cls._serialize,
                    return_schema=core_schema.union_schema([
                        core_schema.timedelta_schema(),
                        core_schema.typed_dict_schema(
                            ser_param_schemas,
                            extra_behavior='forbid',
                            total=False,
                            ),
                        ]),
                    ),
                )
