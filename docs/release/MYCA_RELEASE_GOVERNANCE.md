# MyCA — Repository Governance & Immutable Release Procedures

## 1. Commit & Tag Signing Standard (Required Before `v1.0.18`)

All release commits and tags on `master` must be cryptographically signed so GitHub verification (`verified: true`) holds across the release history.

```bash
# Configure SSH signing (recommended) or GPG signing locally:
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true

# Upload the public signing key to GitHub Settings -> SSH and GPG keys -> Key type: Signing Key.
```

## 2. GitHub Branch Protection Rules (`master`)

Repository administrators must enforce on `master` (GitHub Settings -> Branches -> Branch protection / Ruleset):
- Require a pull request before merging (`>= 1` approval, dismiss stale reviews on new commits).
- Require status checks to pass before merging (`Require branches to be up to date`):
  - `CI / backend`
  - `CI / frontend`
- Require signed commits (`Require signed commits = true`).
- Block force-pushes and branch deletion.
- Restrict direct pushes to `master`.

## 3. Release Approval & Immutable Annotated Tagging

1. Verify `master` HEAD commit is signed (`git log -1 --show-signature`), clean, and has green `CI / backend` and `CI / frontend` runs on GitHub Actions.
2. Confirm release prerequisites match the milestone gate (for `v1.0.18+`, ensure external IdP/JWKS and/or GSP scope matches the approved release target).
3. Create and push an **annotated signed tag** cleanly (never move, overwrite, or force-push historical tags `v1.0.0`–`v1.0.17`):

```bash
git tag -s v1.0.18 -m "MyCA v1.0.18 — <approved release summary>"
git verify-tag v1.0.18
git push origin v1.0.18
```
