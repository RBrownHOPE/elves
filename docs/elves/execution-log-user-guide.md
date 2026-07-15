# Execution log: Elves user guide

## Run digest

- **Started:** 2026-07-15, America/New_York
- **Current phase:** acceptance complete, final readiness pending
- **Active batch:** B0, Build and publish the user guide
- **Last completed batch:** none
- **Next required action:** commit acceptance evidence and run exact-tip final readiness
- **Active PR:** not created yet
- **Docs promoted this run:** `PRODUCT.md`
- **Latest Elves Report:** not generated yet

## Session setup: 2026-07-15

- **Plan:** `docs/plans/v2.5.0-user-guide.md`
- **Survival guide:** `docs/elves/survival-guide-user-guide.md`
- **Execution log:** `docs/elves/execution-log-user-guide.md`
- **Branch:** `codex/html-guide-pages`
- **Mode:** chat-to-land, regular merge commit after final readiness
- **Acceptance staging:** session rows will be generated from the plan before implementation proof
- **Design context:** quiet technical manual, task-first information order, WCAG AA target
- **Writing context:** direct and concrete, with no em dashes or emojis

## Decisions made

- Use one dependency-free HTML file so GitHub Pages can publish it without a site generator.
- Publish the `guide/` directory from this repository with the official Pages actions.
- Use the existing dark navy, teal, and brass identity in a restrained light manual rather than
  copying the report page's decorative layout.
- Treat the public guide as the short path and keep the existing references as deeper contracts.

## Gemini 3.5 clarity review: 2026-07-15

- **Route:** Gemini 3.5 Flash, High reasoning, through the configured Antigravity CLI in read-only
  plan mode. The unauthenticated Gemini CLI was not used after its pre-inference auth failure.
- **Blockers resolved:** explain where to open the host and paste kickoff prompts; define
  `ELVES_SKILL_ROOT` before using the follow command.
- **Important findings resolved:** add Devin CLI to the worker table; use an ordered list for the
  run sequence; explain later landing without claiming a host-specific alias mapping.
- **Clarity findings resolved:** remove model-internal cache jargon, capability jargon, ambiguous
  terminal wording, promotional use of “capable,” and internal gap-packet and goal-evidence terms.
- **Editorial outcome:** the reviewer said a first-time user could not complete the original draft
  because of the two blockers. The revised page now puts those prerequisites before their commands.

## Local browser check: 2026-07-15

- Served `guide/` with the standard library and loaded it in headless Chromium.
- Desktop 1440 by 1000 and mobile 390 by 844 returned HTTP 200 with no console errors.
- Both layouts have no page-level horizontal overflow. The mobile code blocks retain their own
  horizontal scrolling.
- The page has eight task sections, valid local anchors, unique IDs, an ordered run sequence, a
  skip link, visible focus styles, and reduced-motion handling.

## Terminal verification: 2026-07-15

- `verify_repo.py --version 2.5.0 --base-ref v2.4.0` passed compile, shell, JSON, focused evidence,
  consistency, release, public API, installed-bundle, and diff checks.
- The release-scoped intentional Composer CLI default approval was promoted from `Unreleased` to
  `2.5.0`.
- Independent review caught stale Pages action majors and extra blank lines at EOF. The workflow
  now uses the latest released Node 24 action lines: configure v6, upload v5, and deploy v5.
- GitHub Pages is not enabled on the repository yet. The host will enable workflow publishing after
  merge, wait for deployment, and verify the live URL before completing the post-landing actions.

## Independent final review: 2026-07-15

- The reviewer found the stale Pages action majors, release approval scope, trailing whitespace,
  and stale SKILL/AGENTS wording. Each serious finding was fixed in one revision.
- The final review found no remaining P0, P1, or P2 issue.
- One P3 accessibility note asked for copy-button status announcements. The copy controls now mark
  their changing label as a polite live region.

## Final readiness correction: 2026-07-15

- The broad terminal gate passed 993 tests, installed-bundle smoke, public API, links, consistency,
  release, and landing acceptance.
- Its secret scan initially mistook GitHub's required workflow permission `id-token: write` for a
  credential. The verifier now allows only the standard `read`, `write`, or `none` permission words
  for that exact workflow key and still rejects a credential-like value. Both focused cases pass.
- The first rerun correctly refused to attest a dirty tip containing that verifier fix. The fix is
  committed before the exact-tip rerun.
