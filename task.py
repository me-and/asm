from abc import ABC
from datetime import UTC, date, datetime, timedelta
from typing import Any, Container, Iterator, Literal, Optional, Self, assert_never
from uuid import UUID, uuid4
from itertools import chain

from pydantic import (
        AwareDatetime,
        Field,
        GetJsonSchemaHandler,
        SerializationInfo,
        ValidationError,
        ValidationInfo,
        computed_field,
        field_validator,
        model_serializer,
        model_validator,
        )
from pydantic.functional_validators import ModelWrapValidatorHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core.core_schema import CoreSchema, SerializerFunctionWrapHandler

from _type_meta import BaseModel, DatetimeToAware, SingletonToList, add_condition_to_json_schema
from timedelta import RelativeTime
from recurrence import SimpleRecurrence, ComplexRecurrence

TaskState = Literal['todo', 'placeholder', 'done', 'dropped']


# TODO This wants to be using a bunch of context and context manager stack
# handling bullshit so when each instance is validated it can set its _parent
# etc fields itself.


class Tag(BaseModel):
    name: str
    urgency_factor: float = 0

    @model_validator(mode='wrap')
    @classmethod
    def _validate(cls, value: Any, handler: ModelWrapValidatorHandler) -> Self:
        try:
            v = handler(value)
        except ValidationError:
            v = handler({'name': value})
        return v

    @model_serializer(mode='wrap')
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> Any:
        if self.urgency_factor == 0:
            return self.name
        return handler(self)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        return {'anyOf': [json_schema,
                          json_schema['properties']['name'],
                          ]}


class Task(BaseModel):
    title: str
    uuid: UUID = Field(default_factory=uuid4)
    state: TaskState = 'todo'
    created: DatetimeToAware = Field(default_factory=datetime.now,
                                     validate_default=True)
    wait: date | DatetimeToAware | None = None
    due: date | DatetimeToAware | None = None
    ended: Optional[DatetimeToAware] = None
    #repetition_template: Optional[UUID] = None  # TODO
    children: SingletonToList['TaskBase'] = Field(default_factory=list)
    requires: SingletonToList[UUID] = Field(default_factory=list)
    blocks: SingletonToList[UUID] = Field(default_factory=list)
    #contexts: SingletonToList[Context] = Field(default_factory=list)  # TODO
    tags: SingletonToList[str] = Field(default_factory=list)
    #times: SingletonToList[Period] | Iterable[Period] = Field(default_factory=list)  # TODO
    base_urgency: Optional[float] = None
    age_urgency_factor: Optional[float] = None
    age_urgency_max: Optional[float] = None

    _parent: Optional['Task'] = None  # TODO
    _tasklist: 'TaskList'

    @model_validator(mode='after')
    def _clear_ended_if_invalid(self) -> Self:
        if self.state in ('placeholder', 'todo'):
            self.ended = None
        elif self.ended is None and self.state in ('done', 'dropped'):
            self.ended = datetime.now().astimezone()
        return self

    @model_validator(mode='after')
    def _set_children_parents(self) -> Self:
        for t in self.children:
            t._parent = self
        return self

    def _valid_child_states(self) -> Container[TaskState]:
        match self.state:
            case 'placeholder':
                return ('placeholder', 'todo', 'done', 'dropped')
            case 'todo':
                return ('todo', 'done', 'dropped')
            case 'done' | 'dropped':
                return ('done', 'dropped')
            case _:
                assert_never(self.state)

    @model_validator(mode='after')
    def _check_child_states(self) -> Self:
        if not self.children:
            return self

        valid_child_states = self._valid_child_states()
        for child in self.children:
            assert child.state in valid_child_states

        return self

    @classmethod
    def __get_pydantic_json_schema__(cls,
                                     core_schema: CoreSchema,
                                     handler: GetJsonSchemaHandler,
                                     ) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))

        # Placeholder tasks can have any sort of children; todo tasks cannot
        # have placeholders as children; done or dropped tasks can only have
        # done or dropped tasks as children.
        condition = {
            'anyOf': [{'properties': {'state': {'const': 'placeholder'}}},
                      {'properties': {
                          'state': {'const': 'todo'},
                          'children': {'items': {'properties': {'state': {
                              'enum': ['todo', 'done', 'dropped'],
                              }}}},
                          }},
                      {'properties': {
                          'children': {'items': {'properties': {'state': {
                              'enum': ['done', 'dropped'],
                              }}}}}},
                      ]
            }
        return add_condition_to_json_schema(json_schema, condition)

    # TODO Can we fix the typing here with some generic wrangling and
    # assertions?
    def _get_inherited_attribute(self, attr: str) -> Any:
        t: Optional['TaskBase'] = self
        while t is not None:
            v = getattr(t, attr)
            if v is not None:
                return v
            t = t._parent
        return getattr(self._tasklist, attr)

    def _indirectly_blocked_tasks(self) -> Iterator['Task']:
        for task in self._tasklist.all_tasks():
            if self.uuid in task.requires:
                yield task

    def _indirectly_blocking_tasks(self) -> Iterator['Task']:
        for task in self._tasklist.all_tasks():
            if self.uuid in task.blocks:
                yield task

    def blocked_tasks(self) -> Iterator['Task']:
        yield from map(self._tasklist._tasks_by_uuid.__getitem__, self.blocks)
        yield from self._indirectly_blocked_tasks()

    def blocking_tasks(self) -> Iterator['Task']:
        yield from map(self._tasklist._tasks_by_uuid.__getitem__, self.requires)
        yield from self._indirectly_blocking_tasks()

    @property
    def inherited_base_urgency(self) -> float:
        return self._get_inherited_attribute('base_urgency')

    @property
    def inherited_age_urgency_factor(self) -> float:
        return self._get_inherited_attribute('age_urgency_factor')

    @property
    def inherited_age_urgency_max(self) -> float:
        return self._get_inherited_attribute('age_urgency_max')

    @property
    def urgency(self) -> float:
        age = datetime.now().astimezone() - self.created
        age_days = age / timedelta(days=1)
        age_urgency = max(age_days * self.inherited_age_urgency_factor,
                          self.inherited_age_urgency_max)
        return self.inherited_base_urgency + age_urgency

    def done(self) -> None:
        for c in self.children:
            assert c.state not in ('done', 'dropped')
        self.state = 'done'
        self.ended = datetime.now().astimezone()

    def drop(self) -> None:
        for c in self.children:
            assert c.state not in ('done', 'dropped')
        self.state = 'dropped'
        self.ended = datetime.now().astimezone()

    def add_child(self, child: 'Task') -> None:
        assert child.state in self._valid_child_states()
        self.children.append(child)
        child._parent = self


class TaskTemplate(BaseModel):
    title: str
    uuid: UUID = Field(default_factory=uuid4)
    parent: Optional[UUID] = None
    wait: date | DatetimeToAware | RelativeTime | None = None
    due: date | DatetimeToAware | RelativeTime | None = None
    children: SingletonToList['TaskTemplate'] = Field(default_factory=list)
    requires: SingletonToList[UUID] = Field(default_factory=list)
    blocks: SingletonToList[UUID] = Field(default_factory=list)
    #contexts: SingletonToList[Context] = Field(default_factory=list)  # TODO
    tags: SingletonToList[str] = Field(default_factory=list)
    #times: SingletonToList[Period] | Iterable[Period] = Field(default_factory=list)
    base_urgency: Optional[float] = None
    age_urgency_factor: Optional[float] = None
    age_urgency_max: Optional[float] = None

    _tasklist: 'TaskList'
    _schedule: 'TaskRecurrenceSchedule'

    @model_validator(mode='after')
    def _set_children_parents(self) -> Self:
        for t in self.children:
            t._parent = self
        return self


class TaskRecurrenceSchedule(BaseModel):
    uuid: UUID = Field(default_factory=uuid4)
    schedule: RelativeTime | SimpleRecurrence | ComplexRecurrence
    tasks: SingletonToList[TaskTemplate]

    _tasklist: 'TaskList'

    @model_validator(mode='after')
    def _set_task_schedule(self) -> Self:
        to_process = self.tasks[:]
        while to_process:
            task = to_process.pop()
            task._schedule = self
            to_process.extend(task.children)
        return self


class TaskList(BaseModel):
    base_urgency: float = 0
    age_urgency_factor: float = 4/365
    age_urgency_max: Optional[float] = 4
    tasks: SingletonToList[Task]
    recurring_tasks: SingletonToList[TaskRecurrenceSchedule] = Field(default_factory=list)
    tags: SingletonToList[Tag]

    _tasks_by_uuid: dict[UUID, Task]
    _task_schedules_by_uuid: dict[UUID, TaskRecurrenceSchedule]
    _task_templates_by_uuid: dict[UUID, TaskTemplate]
    _tags_by_name: dict[str, Tag]

    @model_validator(mode='after')
    def _set_task_tasklist(self) -> Self:
        self._tasks_by_uuid = {}
        to_process = self.tasks[:]
        while to_process:
            task = to_process.pop()
            task._tasklist = self
            self._tasks_by_uuid[task.uuid] = task
            to_process.extend(task.children)
        return self

    @model_validator(mode='after')
    def _set_schedule_tasklist(self) -> Self:
        self._task_schedules_by_uuid = {}
        self._task_templates_by_uuid = {}
        for schedule in self.recurring_tasks:
            self._task_schedules_by_uuid[schedule.uuid] = schedule
            to_process = schedule.tasks[:]
            while to_process:
                task = to_process.pop()
                task._tasklist = self
                self._task_templates_by_uuid[task.uuid] = task
                to_process.extend(task.children)
        return self

    @model_validator(mode='after')
    def _check_tags(self) -> Self:
        self._tags_by_name = {}
        for tag in self.tags:
            self._tags_by_name[tag.name] = tag
        for task in chain(self.all_tasks(), self.all_task_templates()):
            for tag in task.tags:
                assert tag in self._tags_by_name

    def all_tasks(self) -> Iterator[Task]:
        yield from self._tasks_by_uuid.values()

    def all_task_schedules(self) -> Iterator[TaskRecurrenceSchedule]:
        yield from self._task_schedules_by_uuid.values()

    def all_task_templates(self) -> Iterator[TaskTemplate]:
        yield from self._task_templates_by_uuid.values()
