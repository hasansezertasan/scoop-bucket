# Contributing

Add one package family per pull request. A family may contain a binary manifest
and a `-pipx` shim when both routes expose the same application. Use a concise
Conventional Commit title such as `feat: add example manifest` or
`chore(example): update to 1.2.3`.

Before opening a pull request:

1. Generate manifests with the `scoop-add` scaffolder from
   [`scoop-bucket-skills`](https://github.com/hasansezertasan/scoop-bucket-skills);
   do not hand-write their
   update metadata unless the upstream layout requires it.
2. Review inferred descriptions, licenses, archive paths, executable names, and
   smoke-test commands.
3. Run `mise run generate-readme`.
4. Run `mise install` and `mise run check`.
5. When possible, add the bucket on Windows, install the package, and run its
   smoke test.

Include the commands and results in the pull request's verification section.

## Automated pull requests

The update and boilerplate-drift workflows open pull requests with the repository
secret `WORKFLOW_TOKEN` if present, falling back to `GITHUB_TOKEN`. A pull
request opened or updated with `GITHUB_TOKEN` gets its `pull_request` checks
created but held pending approval; other events the token triggers generally do
not start workflows at all (`workflow_dispatch` and `repository_dispatch`
excepted). Maintainers can approve the pending checks or run the `Tests`
workflow manually via `workflow_dispatch` on the update branch before merging —
but approval alone does not run the title and branch-name lint gates, which
listen on `pull_request_target` and start no run for these pull requests. Push
to the branch, or close and reopen such a pull request, to trigger them, or
configure a `WORKFLOW_TOKEN` secret to start applicable checks automatically.
