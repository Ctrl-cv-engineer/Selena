def register_tools(registry):
    owner = registry.owner
    registry.register("listSkills", lambda **kwargs: owner.listSkills(**kwargs))
    registry.register("manageSkill", lambda **kwargs: owner.manageSkill(**kwargs))
    registry.register("deleteSkill", lambda **kwargs: owner.deleteSkill(**kwargs))
    registry.register("importSkill", lambda **kwargs: owner.importSkill(**kwargs))
    registry.register("exportSkill", lambda **kwargs: owner.exportSkill(**kwargs))
    registry.register("browseSkillMarketplace", lambda **kwargs: owner.browseSkillMarketplace(**kwargs))
