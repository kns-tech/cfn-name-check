# CFN Name Length Check

Worst-case AWS resource name length checking for CloudFormation templates,
live in the editor. Resolves `!Sub` / `!Ref` / `!If` / `!GetAtt` expressions —
including the stack name parsed from your `Description` (`StackName=...`)
convention — and flags any name that can exceed its AWS service limit
*before* CloudFormation rolls back your stack.

Architecture (cfn-lint style): a pip-installable Python core does the analysis;
this extension is a thin wrapper that renders its JSON as native diagnostics.
