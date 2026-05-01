---
name: schedule-manager
description: Create, query, update, and delete user schedule tasks stored in the local SQLite reminder system.
metadata:
  author: dialogue-system
  version: "1.0"
---

# Schedule Manager

Use this skill for reminders, dated tasks, todo items with a reminder time, or requests to view, modify, postpone, complete, cancel, or delete scheduled tasks.

## Workflow
- Create tasks with explicit `TaskDate`, `ReminderTime`, and `TaskContent`.
- Query existing tasks before changing or deleting ambiguous tasks.
- Use update/delete tools instead of pretending to remember schedules in conversation memory.

## Tools
- `createScheduleTask`
- `queryScheduleTasks`
- `updateScheduleTask`
- `deleteScheduleTask`
