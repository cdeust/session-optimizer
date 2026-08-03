# Governance

## Decision model

session-optimizer is currently maintained by `@cdeust`. Changes are proposed
through GitHub issues and pull requests. The maintainer considers user impact,
compatibility, test evidence, security, maintenance cost, and the published
roadmap, then records acceptance or rejection on the pull request. Larger
changes begin with an issue so alternatives can be evaluated before code is
written.

Disagreement is resolved with reproducible evidence and documented trade-offs.
When evidence is insufficient, the smallest reversible change wins or the
proposal remains open until the missing evidence exists.

## Roles and responsibilities

- **Maintainer** — triages issues, reviews and merges changes, manages releases,
  repository settings, security advisories, and the roadmap.
- **Contributor** — proposes focused changes, follows the testing policy,
  responds to review, and reports conflicts of interest.
- **Security reporter** — uses the private channel, preserves confidentiality
  during coordination, and supplies enough evidence to reproduce the issue.

There is no separate committer, security team, or release-manager role today.
New maintainers are appointed in a public governance pull request after a
sustained record of technically sound and respectful contributions.

## Records

Implementation decisions live in pull requests; user-visible changes in
`CHANGELOG.md`; security handling in private advisories and the eventual
release notes; priorities in `docs/ROADMAP.md`; and governance changes in this
document's history.

## Continuity of access

Continuity is **not currently satisfied**. As verified on 2026-08-03,
`@cdeust` is the only repository administrator and the only person able to
accept changes, manage issues, and publish a release. The public MIT-licensed
repository can be forked, but a fork does not provide timely control of this
project's issue tracker, releases, or distribution listings.

The OpenSSF Silver access-continuity criterion will remain unmet until at least
one additional trusted maintainer has the permissions and documented recovery
information needed to create and close issues, accept changes, and publish a
release within one week. Adding a name without granting and testing those
capabilities is not considered continuity.

## Contribution licensing

Contributions are accepted under the MIT license. No CLA or DCO sign-off is
required today; adopting either requires a governance pull request explaining
the need and migration impact.
