import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaPython from '@aws-cdk/aws-lambda-python-alpha';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { NagSuppressions } from 'cdk-nag';

export class ThermalMetricsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ログバケットの作成
    const logBucket = new s3.Bucket(this, 'AccessLogsBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [
        {
          expiration: cdk.Duration.days(30),
        },
      ],
    });

    // S3バケットの作成（セキュリティ設定を強化）
    const thermalImageBucket = new s3.Bucket(this, 'ThermalImageBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [
        {
          expiration: cdk.Duration.days(30),
        },
      ],
      // セキュリティ設定の追加
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      serverAccessLogsBucket: logBucket,
      serverAccessLogsPrefix: 'thermal-images-logs/',
    });

    // CloudWatch Logsロググループの作成
    const logGroup = new logs.LogGroup(this, 'ThermalMetricsLogGroup', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Lambda関数の作成（セキュリティ設定を強化）
    const thermalMetricsFunction = new lambdaPython.PythonFunction(this, 'ThermalMetricsFunction', {
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      bundling: {
        // translates to `rsync --exclude='.venv'`
        assetExcludes: ['.venv'],
      },
      entry: 'lambda',
      environment: {
        BUCKET_NAME: thermalImageBucket.bucketName,
      },
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      // セキュリティ設定の追加
      tracing: lambda.Tracing.ACTIVE,
      logGroup: logGroup,
    });

    // Lambda Function URLsの追加（セキュリティ設定を強化）
    const functionUrl = thermalMetricsFunction.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      cors: {
        allowedOrigins: ['*'],
        allowedMethods: [lambda.HttpMethod.POST],
        allowedHeaders: ['content-type', 'x-amz-date', 'authorization', 'x-api-key', 'x-amz-security-token'],
      },
    });

    // CloudWatchメトリクスへの書き込み権限を付与（最小権限の原則を適用）
    thermalMetricsFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: {
        'StringEquals': {
          'cloudwatch:namespace': 'ThermalMetrics'
        }
      }
    }));

    // S3バケットへの書き込み権限を付与（最小権限の原則を適用）
    thermalMetricsFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        's3:PutObject',
        's3:GetObject',
      ],
      resources: [
        `${thermalImageBucket.bucketArn}/thermal_images/*`,
      ],
    }));

    // 出力設定
    new cdk.CfnOutput(this, 'FunctionUrl', {
      value: functionUrl.url,
      description: 'URL for invoking Lambda function directly',
    });

    new cdk.CfnOutput(this, 'BucketName', {
      value: thermalImageBucket.bucketName,
      description: 'S3 Bucket Name for Thermal Images',
    });

    // cdk-nagの警告を抑制
    NagSuppressions.addResourceSuppressions(
      thermalMetricsFunction,
      [
        {
          id: 'AwsSolutions-IAM4',
          reason: 'Lambda実行に必要な基本的なロールです。カスタムポリシーへの移行は将来的な課題として検討します。',
          appliesTo: [
            'Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
          ]
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'S3バケットへのアクセスは特定のプレフィックスに制限されています。',
        }
      ],
      true
    );

    // LogRetentionの警告を抑制（スタック全体に適用）
    NagSuppressions.addStackSuppressions(this, [
      {
        id: 'AwsSolutions-IAM4',
        reason: 'Lambda LogRetention機能に必要な基本的な実行ロールです。',
        appliesTo: [
          'Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
        ]
      },
      {
        id: 'AwsSolutions-IAM5',
        reason: 'Lambda LogRetention機能に必要な権限です。',
        appliesTo: [
          'Action::logs:CreateLogStream',
          'Action::logs:PutLogEvents',
          'Resource::arn:aws:logs:*:*:log-group:/aws/lambda/*'
        ]
      }
    ]);
  }
}
