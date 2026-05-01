import logging

try:
    from DialogueSystem.services.schedule_system import (
        REMINDER_STATUS_UNREMIND,
        ScheduleValidationError,
        TASK_STATUS_PENDING,
    )
except ImportError:
    from DialogueSystem.services.schedule_system import REMINDER_STATUS_UNREMIND, ScheduleValidationError, TASK_STATUS_PENDING


logger = logging.getLogger(__name__)


def _error_payload(action: str, error: Exception):
    return {
        "ok": False,
        "action": action,
        "error": str(error),
    }


def _create_schedule_task(
    registry,
    TaskDate: str,
    ReminderTime: str,
    TaskContent: str,
    ReminderStatus: str = REMINDER_STATUS_UNREMIND,
    TaskStatus: str = TASK_STATUS_PENDING,
):
    owner = registry.owner
    try:
        task = owner.schedule_repository.create_task(
            task_date=TaskDate,
            reminder_time=ReminderTime,
            task_content=TaskContent,
            reminder_status=ReminderStatus,
            task_status=TaskStatus,
        )
        owner._refresh_due_reminder_cache()
        return {
            "ok": True,
            "action": "create",
            "task": task,
        }
    except (ScheduleValidationError, ValueError, TypeError) as error:
        logger.warning("createScheduleTask failed: %s", error)
        return _error_payload("create", error)
    except Exception as error:
        logger.exception("createScheduleTask failed unexpectedly")
        return _error_payload("create", error)


def _query_schedule_tasks(
    registry,
    TaskId: int = None,
    TaskDate: str = None,
    ReminderStatus: str = None,
    TaskStatus: str = None,
    Limit: int = 20,
):
    owner = registry.owner
    try:
        tasks = owner.schedule_repository.list_tasks(
            task_id=TaskId,
            task_date=TaskDate,
            reminder_status=ReminderStatus,
            task_status=TaskStatus,
            limit=Limit,
        )
        return {
            "ok": True,
            "action": "query",
            "count": len(tasks),
            "tasks": tasks,
        }
    except (ScheduleValidationError, ValueError, TypeError) as error:
        logger.warning("queryScheduleTasks failed: %s", error)
        return _error_payload("query", error)
    except Exception as error:
        logger.exception("queryScheduleTasks failed unexpectedly")
        return _error_payload("query", error)


def _update_schedule_task(
    registry,
    TaskId: int,
    TaskDate: str = None,
    ReminderTime: str = None,
    TaskContent: str = None,
    ReminderStatus: str = None,
    TaskStatus: str = None,
):
    owner = registry.owner
    try:
        task = owner.schedule_repository.update_task(
            TaskId,
            task_date=TaskDate,
            reminder_time=ReminderTime,
            task_content=TaskContent,
            reminder_status=ReminderStatus,
            task_status=TaskStatus,
        )
        owner._refresh_due_reminder_cache()
        if task is None:
            return {
                "ok": False,
                "action": "update",
                "error": f"TaskId {TaskId} does not exist.",
            }
        return {
            "ok": True,
            "action": "update",
            "task": task,
        }
    except (ScheduleValidationError, ValueError, TypeError) as error:
        logger.warning("updateScheduleTask failed: %s", error)
        return _error_payload("update", error)
    except Exception as error:
        logger.exception("updateScheduleTask failed unexpectedly")
        return _error_payload("update", error)


def _delete_schedule_task(registry, TaskId: int):
    owner = registry.owner
    try:
        deleted = owner.schedule_repository.delete_task(TaskId)
        owner._refresh_due_reminder_cache()
        if not deleted:
            return {
                "ok": False,
                "action": "delete",
                "error": f"TaskId {TaskId} does not exist.",
            }
        return {
            "ok": True,
            "action": "delete",
            "task_id": int(TaskId),
        }
    except (ScheduleValidationError, ValueError, TypeError) as error:
        logger.warning("deleteScheduleTask failed: %s", error)
        return _error_payload("delete", error)
    except Exception as error:
        logger.exception("deleteScheduleTask failed unexpectedly")
        return _error_payload("delete", error)


def register_tools(registry):
    registry.register("createScheduleTask", lambda **kwargs: _create_schedule_task(registry, **kwargs))
    registry.register("queryScheduleTasks", lambda **kwargs: _query_schedule_tasks(registry, **kwargs))
    registry.register("updateScheduleTask", lambda **kwargs: _update_schedule_task(registry, **kwargs))
    registry.register("deleteScheduleTask", lambda **kwargs: _delete_schedule_task(registry, **kwargs))
