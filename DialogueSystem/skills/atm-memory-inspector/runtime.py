def register_tools(registry):
    owner = registry.owner
    registry.register(
        "searchAutonomousTaskArtifacts",
        lambda **kwargs: owner.searchAutonomousTaskArtifacts(**kwargs),
    )
    registry.register(
        "readAutonomousTaskArtifact",
        lambda **kwargs: owner.readAutonomousTaskArtifact(**kwargs),
    )
