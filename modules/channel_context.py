from cellar.module_api import ModuleContext, NightlyContext


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        settings = ctx.module_settings.get("channel_context", {})
        label = settings.get("label")
        location = (
            label.strip() if isinstance(label, str) and label.strip()
            else f"{ctx.bottle.irc.network} {ctx.message.target}"
        )
        ctx.prompt_sections.append(
            f"IRC location: {location}"
        )

    async def after_response(self, ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: NightlyContext) -> None:
        return None
