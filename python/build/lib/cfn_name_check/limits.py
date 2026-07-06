"""Curated database of AWS resource *name* properties and their length limits.

Path syntax: dot-separated, "[]" means "every element of this list".
Extensible at runtime: pass extra entries via CLI --extra-limits (JSON file)
or the VS Code setting cfnNameCheck.extraLimits.
"""

# Hand-curated overrides. These WIN over the auto-generated registry set on
# conflict (same type + property) because they encode verified corrections
# and nested paths ("[]") the registry extraction doesn't cover.
# type -> list of (property_path, max_len, note, extra_constraints)
# extra_constraints: dict, currently supports {"lowercase": True}
CURATED = {
    # --- IAM ---
    "AWS::IAM::Role": [
        ("RoleName", 64, "IAM role name", {}),
        ("Policies[].PolicyName", 128, "IAM inline policy name", {}),
    ],
    "AWS::IAM::User": [
        ("UserName", 64, "IAM user name", {}),
        ("Policies[].PolicyName", 128, "IAM inline policy name", {}),
    ],
    "AWS::IAM::Group": [
        ("GroupName", 128, "IAM group name", {}),
        ("Policies[].PolicyName", 128, "IAM inline policy name", {}),
    ],
    "AWS::IAM::ManagedPolicy": [("ManagedPolicyName", 128, "IAM managed policy name", {})],
    "AWS::IAM::InstanceProfile": [("InstanceProfileName", 128, "Instance profile name", {})],

    # --- Storage / data ---
    "AWS::S3::Bucket": [("BucketName", 63, "S3 bucket name", {"lowercase": True})],
    "AWS::DynamoDB::Table": [("TableName", 255, "DynamoDB table name", {})],
    "AWS::RDS::DBInstance": [("DBInstanceIdentifier", 63, "RDS instance identifier", {})],
    "AWS::RDS::DBCluster": [("DBClusterIdentifier", 63, "RDS cluster identifier", {})],
    "AWS::ElastiCache::CacheCluster": [("ClusterName", 50, "ElastiCache cluster name", {})],
    "AWS::EFS::FileSystem": [("FileSystemTags", 0, "", {"skip": True})],  # placeholder, no name prop

    # --- Compute ---
    "AWS::Lambda::Function": [("FunctionName", 64, "Lambda function name", {})],
    "AWS::Lambda::LayerVersion": [("LayerName", 140, "Lambda layer name", {})],
    "AWS::ECS::Cluster": [("ClusterName", 255, "ECS cluster name", {})],
    "AWS::ECS::Service": [("ServiceName", 255, "ECS service name", {})],
    "AWS::ECS::TaskDefinition": [("Family", 255, "ECS task definition family", {})],
    "AWS::EKS::Cluster": [("Name", 100, "EKS cluster name", {})],
    "AWS::Batch::JobQueue": [("JobQueueName", 128, "Batch job queue name", {})],
    "AWS::Batch::ComputeEnvironment": [("ComputeEnvironmentName", 128, "Batch compute env name", {})],

    # --- Containers / registry ---
    "AWS::ECR::Repository": [("RepositoryName", 256, "ECR repository name", {"lowercase": True})],

    # --- Networking / LB ---
    "AWS::ElasticLoadBalancingV2::LoadBalancer": [("Name", 32, "ALB/NLB name", {})],
    "AWS::ElasticLoadBalancingV2::TargetGroup": [("Name", 32, "Target group name", {})],
    "AWS::EC2::SecurityGroup": [("GroupName", 255, "Security group name", {})],

    # --- Messaging / events ---
    "AWS::SNS::Topic": [("TopicName", 256, "SNS topic name", {})],
    "AWS::SQS::Queue": [("QueueName", 80, "SQS queue name (incl. .fifo suffix)", {})],
    "AWS::Events::Rule": [
        ("Name", 64, "EventBridge rule name", {}),
        ("Targets[].Id", 64, "EventBridge target Id", {}),
    ],
    "AWS::Events::EventBus": [("Name", 256, "EventBridge bus name", {})],
    "AWS::Scheduler::Schedule": [("Name", 64, "EventBridge Scheduler name", {})],

    # --- CI/CD ---
    "AWS::CodeBuild::Project": [("Name", 255, "CodeBuild project name", {})],
    "AWS::CodePipeline::Pipeline": [("Name", 100, "CodePipeline name", {})],
    "AWS::CodeCommit::Repository": [("RepositoryName", 100, "CodeCommit repository name", {})],
    "AWS::CodeDeploy::Application": [("ApplicationName", 100, "CodeDeploy application name", {})],
    "AWS::CodeDeploy::DeploymentGroup": [("DeploymentGroupName", 100, "CodeDeploy deployment group", {})],

    # --- API / edge ---
    "AWS::ApiGatewayV2::Api": [("Name", 128, "API Gateway v2 API name", {})],
    "AWS::ApiGateway::RestApi": [("Name", 128, "API Gateway REST API name", {})],
    "AWS::AppSync::GraphQLApi": [("Name", 65536, "AppSync API name", {})],
    "AWS::CloudFront::Function": [("Name", 64, "CloudFront function name", {})],

    # --- Observability ---
    "AWS::Logs::LogGroup": [("LogGroupName", 512, "CloudWatch log group name", {})],
    "AWS::CloudWatch::Alarm": [("AlarmName", 255, "CloudWatch alarm name", {})],
    "AWS::CloudWatch::Dashboard": [("DashboardName", 255, "CloudWatch dashboard name", {})],

    # --- Orchestration ---
    "AWS::StepFunctions::StateMachine": [("StateMachineName", 80, "Step Functions state machine", {})],

    # --- Secrets / params ---
    "AWS::SecretsManager::Secret": [("Name", 256, "Secrets Manager secret name", {})],
    "AWS::SSM::Parameter": [("Name", 2048, "SSM parameter name", {})],
    "AWS::KMS::Alias": [("AliasName", 256, "KMS alias name", {})],

    # --- Cognito / auth ---
    "AWS::Cognito::UserPool": [("UserPoolName", 128, "Cognito user pool name", {})],

    # --- Kinesis / analytics ---
    "AWS::Kinesis::Stream": [("Name", 128, "Kinesis stream name", {})],
    "AWS::KinesisFirehose::DeliveryStream": [("DeliveryStreamName", 64, "Firehose delivery stream", {})],
    "AWS::Glue::Job": [("Name", 255, "Glue job name", {})],

    # --- CloudFormation / misc ---
    "AWS::CloudFormation::Stack": [("StackName", 128, "Nested stack name", {})],
    "AWS::AutoScaling::AutoScalingGroup": [("AutoScalingGroupName", 255, "ASG name", {})],
    "AWS::EC2::LaunchTemplate": [("LaunchTemplateName", 128, "Launch template name", {})],
    "AWS::Backup::BackupVault": [("BackupVaultName", 50, "Backup vault name", {})],
    "AWS::Route53::HostedZone": [("Name", 1024, "Hosted zone name", {})],
}


try:
    from .limits_generated import GENERATED
except ImportError:  # snapshot missing (source checkout without generation)
    GENERATED = {}


def _merged():
    """Layer 1: registry-generated. Layer 2: curated (wins on same type+prop)."""
    out = {t: list(entries) for t, entries in GENERATED.items()}
    for rtype, entries in CURATED.items():
        existing = out.setdefault(rtype, [])
        for cur in entries:
            existing[:] = [e for e in existing if e[0] != cur[0]]
            existing.append(cur)
    return out


LIMITS = _merged()


def merge_extra(extra: dict):
    """Layer 3: user-supplied limits {"AWS::X::Y": [["Prop", 64, "note"], ...]} — win over everything."""
    for rtype, entries in (extra or {}).items():
        cur = LIMITS.setdefault(rtype, [])
        for e in entries:
            path, limit = e[0], int(e[1])
            note = e[2] if len(e) > 2 else "user-defined limit"
            cur[:] = [x for x in cur if x[0] != path]
            cur.append((path, limit, note, {}))
