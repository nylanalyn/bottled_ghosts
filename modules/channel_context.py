from cellar.module_api import ModuleContext


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        ctx.prompt_sections.append(
            f"IRC location: {ctx.bottle.irc.network} {ctx.message.target}"
        )

    async def after_response(self, ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: ModuleContext) -> None:
        return None
