# Agent prompt overrides

One optional `<agent>.txt` file per agent. When a file is present here its
contents replace that agent's built-in prompt on the next pipeline run; delete
the file to go back to the default.

Recognised agent keys: `quality_gate`, `categorisation`, `summarisation`,
`scoring`, `qa`.

Edit these from the dashboard (**Settings → Agent prompts**) rather than by
hand — it validates that the required `{placeholders}` are still present before
saving. Managed by `dipt/prompt_store.py`.
