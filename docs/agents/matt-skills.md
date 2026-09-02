# Pinned Matt Pocock engineering skills

The builder uses the real `mattpocock-skills` Claude plugin, not copied prompt fragments.

Pinned upstream revision: `mattpocock/skills@0ab1b63a410a03d3627979a109c8695de27af954`.

`.claude/settings.json` registers that exact marketplace revision and enables `mattpocock-skills@mattpocock`. Plugin skills are invoked by their namespaced IDs such as `mattpocock-skills:tdd`.

For a new headless factory host, install the project plugin once before dispatching work:

```bash
claude plugin marketplace add mattpocock/skills@0ab1b63a410a03d3627979a109c8695de27af954 --scope project
claude plugin install mattpocock-skills@mattpocock --scope project
```

The workflow preflight fails closed if the plugin is absent. Updating the pinned SHA is an explicit reviewed dependency change.
