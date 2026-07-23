# Codex integration boundary

Byte Core is independent and is not affiliated with or endorsed by OpenAI. Its v0.1 Codex integration uses documented repository-native surfaces and remains optional to Core lifecycle behavior.

## Supported surface map

| Surface | v0.1 status | Ownership and purpose |
| --- | --- | --- |
| Repository `AGENTS.md` | Supported | Public durable repository layout, validation, ownership, and safety guidance |
| Project `.codex/config.toml` | Supported when the user trusts the project | Enables only the repository SessionStart hook |
| Project SessionStart hook | Advisory proof | Emits generic safety context; never blocks tools, mutates files, or collects private context |
| Byte skill | Deferred | No stable repeated workflow currently benefits from duplicating the CLI and `AGENTS.md` contracts |
| Plugin package | Deferred beyond repository-native MVP validation | No v0.1 plugin, marketplace, app, bundled credential, or auto-install behavior |
| MCP server or app connector | Not required | Core behavior remains local and useful without a connector |
| Codex memory or transcript | Unsupported authority | Never parsed or treated as deployment truth |

The repository does not select a model, reasoning level, provider, credential method, approval policy, sandbox mode, MCP server, or personal configuration. Those remain user or administrator choices.

## Trust and degradation

Codex loads project `.codex/` configuration and project hooks only after the project is trusted. In an untrusted project—or in a Codex version or surface that does not load the configuration—the Python CLI, validators, plans, tests, documentation, and shell assets continue to work unchanged.

The project configuration enables the stable hooks feature and registers one `SessionStart` command. It does not broaden filesystem or network access and does not bypass approvals.

## SessionStart hook

The hook reads one bounded JSON object from standard input. It checks only:

- `hook_event_name` equals `SessionStart`; and
- `source` is one of `startup`, `resume`, `clear`, or `compact`.

It deliberately ignores session IDs, working-directory values, model names, permission metadata, and `transcript_path`. It never opens the transcript or any deployment file. Valid input produces one generic repository-safety `systemMessage`.

Malformed, oversized, unknown, and future input exits successfully with a generic fallback message directing Codex to `AGENTS.md`. This keeps the hook advisory and prevents failure loops. It writes no file, report, log, cache, or network request.

## Testing and compatibility

Tests parse project TOML, validate the configured hook path and event, run public fictional fixtures through the hook, prove the transcript path is ignored, and verify malformed/future events degrade without failure.

Hook schemas can evolve. Byte accepts only the small documented subset it needs and ignores extra keys. Changes to the hook event or output contract require a new reviewed integration slice. Core functionality must never depend on hook execution.

## Skills and plugins

The v0.1 workflows are already expressed as exact CLI plans and repository guidance. Packaging a skill now would create a second behavioral authority without a proven reusable workflow, so it is explicitly deferred.

Plugin packaging is also deferred. A future plugin must be justified by repository-native MVP evidence, use a valid public manifest, preserve project trust and hook review, remain optional, and introduce no private connector dependency. The public repository will not imply installation, endorsement, or availability before that work is separately approved.
