def register_tools(registry):
    owner = registry.owner
    registry.register("delegateTask", lambda **kwargs: owner.delegateTask(**kwargs))
    registry.register("delegateTasksParallel", lambda **kwargs: owner.delegateTasksParallel(**kwargs))
    registry.register("continueDelegatedTask", lambda **kwargs: owner.continueDelegatedTask(**kwargs))
    registry.register("cancelDelegatedTask", lambda **kwargs: owner.cancelDelegatedTask(**kwargs))
    registry.register("getDelegatedTaskStatus", lambda **kwargs: owner.getDelegatedTaskStatus(**kwargs))
    registry.register("listDelegatedTasks", lambda **kwargs: owner.listDelegatedTasks(**kwargs))
    registry.register("waitForDelegatedTasks", lambda **kwargs: owner.waitForDelegatedTasks(**kwargs))
