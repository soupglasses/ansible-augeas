# Contributing

Contributions should preserve the generic tree contract documented in the
README and must not add an imperative `augtool` command interface.

Develop the repository at an Ansible collection path:

```text
collections/ansible_collections/soupglasses/augeas
```

Install Augeas, its lenses, `python-augeas`, and a supported `ansible-core`,
then run:

```shell
ansible-test sanity
ansible-test units
ansible-test integration augeas
ansible-galaxy collection build
```

The unit and integration suites require `python-augeas`, libaugeas, and the
stock Augeas lenses. Missing runtime dependencies are test failures, not
skips. The nginx validation block is optional on a development machine; CI
installs nginx and always runs it.

Tests should cover the pure YAML codec and, when behavior depends on a lens,
exercise the actual Augeas library against a temporary filesystem root. Add an
integration assertion for user-visible module behavior.

Use a changelog fragment under `changelogs/fragments/` for every externally
visible change.

## Release process

Set the release version in `galaxy.yml`. Keep each module's `version_added`
value at the version where that module first appeared.

Generate release notes from the accumulated fragments:

```shell
python -m pip install antsibull-changelog
antsibull-changelog lint
antsibull-changelog release --reload-plugins
```

The release command updates `changelogs/changelog.yaml`, writes
`CHANGELOG.rst`, and consumes the included fragments. Review both generated
files, then run the complete test and build commands above.

Commits use Conventional Commits and are signed. Create an annotated signed
tag whose name matches the version in `galaxy.yml`:

```shell
git tag -s 1.0.0 -m "Release 1.0.0"
git verify-tag 1.0.0
git push origin main
git push origin 1.0.0
```

Create a GitHub Release from that tag after CI succeeds. Publishing a stable
GitHub Release runs `.github/workflows/release.yml`, which verifies the version,
builds the collection, and publishes it to Ansible Galaxy with the
`GALAXY_API_KEY` repository secret. Draft and prerelease releases are not
published.
