# Licensing audit

This is an engineering inventory, not legal advice. Legal review is required
before commercial distribution or changing the repository license.

## Current evidence

As audited on 2026-07-27, `git shortlog -sne --all` reports 96 commits by
`wtbates99 <wtbates99@gmail.com>` and 6 by
`WB <123271976+wtbates99@users.noreply.github.com>`. These appear to be two
identities for William Bates, but identity and employment/assignment ownership
must be confirmed. The root `LICENSE` is CC BY-NC 4.0, `pyproject.toml` declares
that license, and documentation names William Bates as copyright holder.

No vendored source tree or explicit copied-code attribution was found by the
repository text audit. Generated artifacts include distribution archives, compiled
Python caches, local manifests, and example fixture data/scripts. The JPEG logo has
no embedded repository attribution; its creator and source must be confirmed.
Git history and issue/PR discussions still require manual review for copied code,
AI-generated code terms, employer claims, and third-party contributions.

Dependencies remain under their own licenses and notices. The current direct
runtime set is PyYAML, Click, Flask, OpenAI, Rich, and sqlglot, with
database/storage/keyring extras. A release audit must collect their resolved
versions, licenses, notices, and transitive obligations from the lockfile/build.

## Decision and risks

Do not claim that a future license change revokes rights already granted for
previously distributed CC BY-NC versions. Do not remove third-party notices. Before
adopting an all-rights-reserved notice, confirm sole ownership, logo provenance,
contributor identity/assignment, copied/generated code provenance, and dependency
obligations. Establish contributor terms before accepting outside contributions.

If ownership is confirmed, a future version may use:

> Copyright © 2026 William Bates. All rights reserved.
>
> This source code is proprietary. No permission is granted to use, copy, modify,
> distribute, sublicense, or create derivative works without prior written
> authorization.

Unresolved risks: identity/assignment confirmation, prior-release scope, logo
ownership, generated-code provenance, dependency notice completeness, and any
unrecorded external contributions.
