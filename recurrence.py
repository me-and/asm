from datetime import datetime
from abc import ABC
from typing import (
        Annotated,
        Any,
        Iterator,
        Optional,
        Self,
        overload,
        )

from pydantic import (
        AwareDatetime,
        ConfigDict,
        Field,
        GetJsonSchemaHandler,
        model_validator,
        )
from pydantic.json_schema import JsonSchemaValue
from pydantic_core.core_schema import CoreSchema
import dateutil.rrule as rrule
from annotated_types import Ge, Le

from _type_meta import (
        BaseModel,
        DatetimeToAware,
        IntEnumSchema,
        NotZero,
        SingletonToList,
        add_condition_to_json_schema,
        )
from weekday import WeekdayName, _dateutilWeekdayOffsetAnnotation


WeekdayOffset = Annotated[rrule.weekday, _dateutilWeekdayOffsetAnnotation]


class Frequency(IntEnumSchema):
    YEARLY = rrule.YEARLY
    MONTHLY = rrule.MONTHLY
    WEEKLY = rrule.WEEKLY
    DAILY = rrule.DAILY
    HOURLY = rrule.HOURLY
    MINUTELY = rrule.MINUTELY
    SECONDLY = rrule.SECONDLY


class _BaseRecurrence(BaseModel, ABC):
    _rrule: rrule.rrule | rrule.rruleset

    model_config = ConfigDict(populate_by_name=True,
                              extra='forbid',
                              frozen=True,
                              )

    @overload
    def __getitem__(self, item: int) -> AwareDatetime: ...
    @overload
    def __getitem__(self, item: slice) -> list[AwareDatetime]: ...
    def __getitem__(self, item: int | slice
                    ) -> datetime | list[AwareDatetime]:
        return self._rrule.__getitem__(item)

    def __iter__(self) -> Iterator[AwareDatetime]:  # type: ignore[override]
        return self._rrule.__iter__()

    def __contains__(self, item: AwareDatetime) -> bool:
        return self._rrule.__contains__(item)

    def count(self) -> int:
        return self._rrule.count()

    def before(self, dt: AwareDatetime, inc: bool = False,
               ) -> Optional[AwareDatetime]:
        return self._rrule.before(dt, inc)

    def after(self, dt: AwareDatetime, inc: bool = False,
              ) -> Optional[AwareDatetime]:
        return self._rrule.after(dt, inc)

    def xafter(self, dt: AwareDatetime,
               count: Optional[Annotated[int, Ge(1)]] = None,
               inc: bool = False) -> Iterator[AwareDatetime]:
        return self._rrule.xafter(dt, count, inc)

    def between(self,
                after: AwareDatetime,
                before: AwareDatetime,
                inc: bool = False,
                count: Any = 1,  # Not actually used AFAICS
                ) -> list[AwareDatetime]:
        return self._rrule.between(after, before, inc, count)


class SimpleRecurrence(_BaseRecurrence):
    freq: Frequency
    dtstart: Optional[DatetimeToAware] = None
    interval: Annotated[int, Ge(1)] = 1
    wkst: Optional[WeekdayName] = None
    count_limit: Annotated[Optional[Annotated[int, Ge(1)]],
                           Field(alias='count'),
                           ] = None
    until: Optional[DatetimeToAware] = None
    bysetpos: Optional[SingletonToList[
        NotZero[Annotated[int, Ge(-366), Le(366)]]]] = None
    bymonth: Optional[SingletonToList[
        Annotated[int, Ge(1), Le(12)]]] = None
    bymonthday: Optional[SingletonToList[
        NotZero[Annotated[int, Ge(-31), Le(31)]]]] = None
    byyearday: Optional[SingletonToList[
        NotZero[Annotated[int, Ge(-366), Le(366)]]]] = None
    byeaster: Optional[SingletonToList[int]] = None
    byweekno: Optional[SingletonToList[
        NotZero[Annotated[int, Ge(-53), Le(53)]]]] = None
    byweekday: Optional[SingletonToList[WeekdayOffset]] = None
    byhour: Optional[SingletonToList[
        Annotated[int, Ge(0), Le(23)]]] = None
    byminute: Optional[SingletonToList[
        Annotated[int, Ge(0), Le(59)]]] = None
    bysecond: Optional[SingletonToList[
        Annotated[int, Ge(0), Le(59)]]] = None

    _rrule: rrule.rrule

    @model_validator(mode='after')
    def _after_validator(self) -> Self:
        assert not hasattr(self, '_rrule')
        if self.count_limit is not None and self.until is not None:
            raise ValueError(f'Cannot set both count and until')
        self._rrule = rrule.rrule(freq=self.freq,
                                  dtstart=self.dtstart,
                                  interval=self.interval,
                                  wkst=self.wkst,
                                  count=self.count_limit,
                                  until=self.until,
                                  bysetpos=self.bysetpos,
                                  bymonth=self.bymonth,
                                  bymonthday=self.bymonthday,
                                  byyearday=self.byyearday,
                                  byeaster=self.byeaster,
                                  byweekno=self.byweekno,
                                  byweekday=self.byweekday,
                                  byhour=self.byhour,
                                  byminute=self.byminute,
                                  bysecond=self.bysecond,
                                  )
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls,
                                     core_schema: CoreSchema,
                                     handler: GetJsonSchemaHandler,
                                     ) -> JsonSchemaValue:
        json_schema = handler(core_schema)
        json_schema = handler.resolve_ref_schema(json_schema)

        # Need to encode that having both count and until is invalid, i.e. at
        # least one of the two must be None or completely unset.
        condition: JsonSchemaValue
        condition = {'anyOf': [{'properties': {'until': False}},
                               {'properties': {'count': False}},
                               ]}

        # If we're validating, it's also acceptable for the inputs to be
        # specified as null.
        if handler.mode == 'validation':
            condition['anyOf'].append(
                    {'properties': {'until': {'type': 'null'}}})
            condition['anyOf'].append(
                    {'properties': {'count': {'type': 'null'}}})

        json_schema = add_condition_to_json_schema(json_schema, condition)

        return json_schema


class ComplexRecurrence(_BaseRecurrence):
    rrules: list[SimpleRecurrence] = Field(default_factory=list)
    exrules: list[SimpleRecurrence] = Field(default_factory=list)
    rdates: list[DatetimeToAware] = Field(default_factory=list)
    exdates: list[DatetimeToAware] = Field(default_factory=list)

    _rrule: rrule.rruleset

    @model_validator(mode='after')
    def _after_validator(self) -> Self:
        assert not hasattr(self, '_rrule')

        self._rrule = rrule.rruleset()

        for rrule in self.rrules:
            self._rrule.rrule(rrule._rrule)
        for rdate in self.rdates:
            self._rrule.rdate(rdate)
        for exrule in self.exrules:
            self._rrule.exrule(exrule._rrule)
        for exdate in self.exdates:
            self._rrule.exdate(exdate)

        return self
