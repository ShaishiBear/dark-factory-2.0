# Preparing a repository migration

The migration preparation command is an offline operator tool. It does not detach,
delete, rename or privatize a repository, upload data, dispatch work, modify a live
state directory, import approval or grant execution authority.

```sh
python scripts/prepare_repository_migration.py --archive /private/archive \
  --output /private/new-preparation --account-plan free
```

Supply an archive produced by the operator's GitHub export: `manifest.json` lists
each relative file path, byte size and SHA-256; `repository.json` and
`repository-at-end.json` bind the numeric identity and full name;
`git-restore-check.json` records the restored main SHA. Required files are pinned
in the tool. All listed content is rehashed; missing files, links, escaping paths,
duplicate paths/JSON fields, changed repository identities and export errors refuse.
The manifest itself must be retained through the owner's trusted backup process;
hashes do not authenticate GitHub authorship or turn copied records into proof.

The output contains a disposable checkout reconstructed from the verified bundle,
an empty intent directory, and a patch removing only the old active programme file
from that checkout. The historical programme remains in the archive and Git
history. No old carry notes, approvals, jobs, publication nonces, receipts or spend
records are loaded into the new state. Old state must be retained unchanged, and
any pending/uncertain charges must be reconciled before cutover; empty new state is
not permission to reset or spend an old budget.

`preparation.json` always says `cutover_ready: false` and
`execution_authority: false`. It records remaining operator prerequisites. A paid
plan declaration is not evidence that required checks are enforced. GitHub Free
cannot supply the current factory's private-repository branch protections.

## Actual switch procedure

1. Coordinate a quiet window with task and deployment owners. Disable new remote
   effects and verify no running workflow, pending hosted call or unresolved paid
   reservation. Retain a stop outside GitHub: remote stop issues may disappear.
2. Refresh the code/metadata/archive and take a consistent private host snapshot.
   Keep original issue/PR/comment/run identities, source SHAs and private event
   chains. Recover required credentials without printing them or using Actions
   to exfiltrate secret values. Verify encrypted backup recovery off-host.
3. Review the generated bootstrap patch through the protected maintainer lane.
   It is not a new product intent or authority to resume the inherited programme.
   Preserve all existing tests, holdouts, identity checks and merge requirements.
4. Apply the selected repository transition only after explicitly addressing its
   native metadata loss. Bind the old identity/export digest and observed new
   numeric identity in a private operator receipt, even if the name is unchanged.
   Do not reuse an old success receipt for a new repository with the same name.
5. Restore labels/configuration/App access/secrets as required. Keep autonomous
   workflows disabled while verifying private protections, required checks,
   authenticated host Git access and the owner's actual Actions spending limit.
6. Switch the service to a separate empty state directory only under deployment
   ownership. Expire old publication consent rather than rebinding it. Establish
   an independently checked baseline and obtain fresh explicit product approval.
   Imported open issues remain historical/unadmitted until individually reviewed.
7. Observe a new bounded approved execution through the unchanged qualification
   and merge process before returning to unattended operation.

Native GitHub issue/PR/run identities cannot be restored by copying JSON. Archive
replay can explain historical outcomes; it cannot authorize new ones. Expired
artifacts and uncollected logs must stay recorded gaps. The one-off migration
receipt is an operator record, not an alternate judge or runtime acceptance path.

References: [GitHub backup limitations](https://docs.github.com/en/repositories/archiving-a-github-repository/backing-up-a-repository),
[detaching a fork](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/detaching-a-fork),
[protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
