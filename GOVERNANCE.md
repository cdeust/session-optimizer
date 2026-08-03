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

The project can continue without credentials held by the current maintainer.
Its complete source, history, tests, marketplace manifests, release workflow,
and documentation are public under MIT. A successor can fork the repository,
enable Issues, accept pull requests into the fork, and publish a tagged release
through the committed workflow under the fork's own GitHub OIDC identity. No
original signing key, package-registry token, domain, private dependency, or
legal assignment is required. Users can install from the successor's public
repository and marketplace URL.

This provides the three OpenSSF continuity capabilities within a week: create
and close issues on the continuation repository, accept proposed changes, and
release a version. Past releases remain independently verifiable through their
published checksums and attestations. The procedure is:

1. fork the complete public repository under the successor's account or
   organization and enable its issue tracker;
2. publish a continuity notice naming the former repository and the new
   canonical URL;
3. accept changes through the unchanged CI-gated pull-request process; and
4. create a semantic-version tag, let the committed release workflow attest
   the artifacts under the fork identity, and publish the new install URL.

As verified on 2026-08-03, `@cdeust` remains the only administrator of the
current GitHub repository. That makes the bus factor 1 and means its original
URL and listings cannot be transferred without the account; it does not make
the MIT-licensed project results or their issue/change/release process
non-continuable. Adding a second trusted maintainer is still the preferred way
to preserve the existing identity with even less interruption.

## Contribution licensing

Contributions are accepted under the MIT license. No CLA or DCO sign-off is
required today; adopting either requires a governance pull request explaining
the need and migration impact.
