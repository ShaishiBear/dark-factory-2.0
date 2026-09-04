# Engineering methods for factory workers

Autonomous factory workers do **not** load the `mattpocock-skills` plugin, or any plugin. The kernel launches every model worker with `--bare`, an empty strict MCP configuration and slash commands disabled (`factory_kernel/providers.py`), so no project settings, hooks, plugins or skills reach it. That isolation is deliberate and is part of the trust root.

The engineering disciplines those skills describe reach workers as **plain, pinned instruction text** instead:

- `.factory/methods/manifest.json` lists each method, its source (`mattpocock/skills` at the pinned upstream revision `0ab1b63a410a03d3627979a109c8695de27af954`, `ponytail`, or `dark-factory`), how it was adapted, and which roles receive it.
- `.factory/methods/*.md` holds the adapted text. `factory_kernel/methods.py` validates the manifest fail-closed and the kernel injects the matching text into each role's prompt between the role prompt and the run context.
- The directory is protected by the security guard: only the human maintenance lane changes method text, and a change to it is a reviewed trust-root change.

`.claude/settings.json` still registers the Matt Pocock marketplace and enables the plugin. That configuration is for **interactive human sessions** in this checkout only. The factory never reads it, and there is no preflight for the plugin because the factory does not depend on it.

An earlier version of this file said the builder used the real plugin and that a preflight failed closed without it. Neither was true of autonomous execution; see `.factory/decisions.md` D-013.
