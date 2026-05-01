def register_tools(registry):
    owner = registry.owner
    registry.register("getSelfLog", lambda **kwargs: owner.getSelfLog(**kwargs))
