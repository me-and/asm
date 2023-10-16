from datetime import date, datetime, timedelta
from typing import Container

from pydantic import AwareDatetime, model_validator
from pydantic.functional_validators import ModelWrapValidatorHandler

from _type_meta import BaseModel, DatetimeToAware, make_datetime_aware
from timedelta import RelativeTime


class Period(BaseModel, Container[date]):
    start: date | DatetimeToAware
    end: date | DatetimeToAware | RelativeTime

    def real_end(self) -> date | AwareDatetime:
        if isinstance(self.end, RelativeTime):
            return self.start + self.end
        return self.end

    @model_validator(mode='after')
    def _check_start_vs_end(self) -> Self:
        start = self.start
        end = self.real_end()
        if isinstance(start, datetime) and isinstance(end, datetime):
            assert start < end
        else:
            assert self._get_date(start) <= self._get_date(end)
        return self

    @staticmethod
    def _get_date(d: date) -> date:
        if isinstance(d, datetime):
            return d.date()
        return d

    def __contains__(self, other: object) -> bool:
        if not isinstance(other, date):
            return False

        if isinstance(other, datetime):
            other = make_datetime_aware(other)

        if isinstance(self.start, datetime) == isinstance(other, datetime):
            if self.start > other:
                return False
        elif self._get_date(self.start) > self._get_date(other):
            return False

        end = self.real_end()
        if isinstance(end, datetime) == isinstance(other, datetime):
            if end < other:
                return False
        elif self._get_date(end) < self._get_date(other):
            return False

        return True
