# Provider-neutral production delivery

Keep production delivery separate from the project-management adapter. The
tracker says what is ready; `bin/release-feature.py` executes an exact immutable
release through structured project hooks.

## Preconditions

The executor refuses unless every [task] is in a terminal, commit-requiring
status, the feature branch exists and is clean in its integration worktree, and
no deployment transaction for another commit is active. `[Ready to deploy]`
continues to mean reviewed, validated, and integrated—not already deployed.

`config/deployment.config.json` is disabled by default. To opt in, configure
JSON argv arrays for every required hook and select one mode:

- `approval-required` — `verifyApproval` must validate an external authorization
  bound to the generated manifest. Agents cannot create that authorization.
- `automatic` — the trusted config pre-authorizes only a non-destructive release
  plan for the exact reviewed commit. Policy-sensitive plan changes still fail.

Do not put shell strings, credentials, or provider-specific logic in the skill.
Each hook is an argv array and may use these placeholders:

`{repository}`, `{feature_id}`, `{team}`, `{commit}`, `{environment}`,
`{release_id}`, `{plan_file}`, `{manifest_file}`, `{transaction_file}`.

## Hook contract

1. `plan` writes `{plan_file}` as JSON:

   ```json
   {
     "schemaVersion": 1,
     "environment": "production",
     "commit": "<full hash>",
     "artifactDigest": "sha256:<digest>",
     "changes": [
       {"action": "UPDATE", "resourceType": "service", "resourceId": "api"}
     ],
     "rollback": {
       "automaticSafe": true,
       "previousArtifactDigest": "sha256:<digest>"
     }
   }
   ```

2. `status` prints one JSON object with `state` equal to `not-applied`,
   `in-progress`, `applied`, or `failed`, plus the observed artifact digest when
   available. This query makes crash recovery safe; an uncertain apply is never
   blindly repeated.
3. `apply` deploys only `{release_id}` and the digest in the immutable plan.
4. `verify` independently checks production health and version.
5. `rollback` is optional and is callable automatically only for the immediately
   previous artifact declared by the plan.

The plan may describe `CREATE`, `READ`, `UPDATE`, `REPLACE`, or `DELETE` changes.
Unknown actions are denied. Autonomous `DELETE`/`REPLACE`, database/IAM/network/
secret mutations, and above-threshold cost changes never reach `apply`.

## Transaction and credentials

The durable phases are `planned -> applying -> verifying -> succeeded`, with
`failed`, `rolling-back`, and `rolled-back` outcomes. The transaction stores the
commit, artifact digest, plan digest, release id, timestamps, hook results, and
redacted log paths. Re-running the same release is idempotent.

The executor builds a clean environment from `environmentAllowlist` and an
optional mode-0600 `credentialEnvFile`. Ordinary agents and the PM supervisor
must not inherit that credential file's values. Logs redact every loaded value
before persistence or tracker projection.

After independent verification succeeds, the executor upserts the [feature]'s
`[deployment]` projection and moves the [feature] to its configured terminal
status. A failed verification is never reported as success; safe rollback or an
`[escalation]` is the only next action.
