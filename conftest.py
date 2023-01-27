import os
from typing import Iterator
from unittest.mock import patch
import boto3 as boto3
from botocore.config import Config
import pytest as pytest
import requests as requests

from moto import settings
from moto.core.models import BaseMockAWS
from tests import DEFAULT_ACCOUNT_ID



# overriding ENVs + settings to use LocalStack as endpoint
os.environ["TEST_SERVER_MODE_ENDPOINT"] = 'http://localhost:4566'
os.environ["MOTO_CALL_RESET_API"] = 'false'
settings.TEST_SERVER_MODE = True

# TODO could also use fixture to override endpoint_url
@pytest.fixture(scope="session", autouse=True)
def default_localstack_client_config_fixture() -> Iterator[None]:
    def localstack_client(*args, org=boto3.client, **kwargs):
        #kwargs["endpoint_url"] = 'http://localhost:4566'
        config: Config = kwargs.get("config")
        max_retry_config = Config(
            retries={"max_attempts": 1, "total_max_attempts": 2})
        if config:
            config.merge(max_retry_config)
        else:
            kwargs["config"] = max_retry_config
        return org(*args, **kwargs)
    with patch("boto3.client", side_effect=localstack_client):
        yield

@pytest.fixture(scope="session", autouse=True)
def default_localstack_client_fixture() -> Iterator[None]:
    """
    Fixture that will batch the AWS_ACCESS_KEY_ID to use moto's DEFAULT_ACCOUNT_ID
    This is required, because some tests explicelty check the returned arn, and by default LocalStack will assume ID 00000000
    """
    def localstack_mock_env_variables(self, org=BaseMockAWS.mock_env_variables):
        # patch the access key id to use the same one in LocalStack
        self.FAKE_KEYS["AWS_ACCESS_KEY_ID"] = DEFAULT_ACCOUNT_ID
        org(self)

    with patch("moto.core.models.BaseMockAWS.mock_env_variables", autospec=True, side_effect=localstack_mock_env_variables):
        yield


@pytest.fixture(scope="function", autouse=True)
def default_cleanup_localstack_resources():
    """
    Fixture to cleanup localstack resources after each test case run
    We do not know which interservice communication happend, so we delete all resources, by sending a request to
    the endpoint /_pods/state/reset
    """
    yield
    # cleanup
    payload = {'persistence': True} # defining no services means all services will be cleared (we don't know which interservice communication tests trigger)
    headers = {'content-type': 'application/json'}
    url = "http://localhost:4566/_pods/state/reset"
    requests.delete(
        url,
        json=payload,
        headers=headers,
        timeout=90
    )

@pytest.fixture(scope="function", autouse=True)
def check_if_test_failed(request):
    cur_failed = request.session.testsfailed
    yield
    now_failed = request.session.testsfailed

    if cur_failed < now_failed:
        # do something
        # teardown may still fail, but that should be fine and can be ignored
        print("--> test failed")


# probably not required, as we will not need a switch
# def pytest_addoption(parser):
#     group = parser.getgroup("motofallback")
#     group.addoption(
#         "--fallback",
#         action="store_true",
#         help="Run Tests with Moto Fallback for LocalStack",
#     )

def pytest_collection_modifyitems(items, config):
    #if config.option.fallback is False:
    #   return

    selected_items = []
    deselected_items = []

    # response = requests.get("http://localhost:4566/_localstack/health").content.decode("utf-8")
    # available_services = [k for k in json.loads(response).get("services").keys()]
    available_services = ['ec2', 'es', 'events', 'firehose', 'iam', 'kinesis', 'kms', 'lambda', 'logs', 'opensearch', 'redshift', 'resource-groups', 'resourcegroupstaggingapi', 'route53', 'route53resolver']  # just for initial testing in CI
    # 'acm', 'apigateway', 'cloudformation', 'cloudwatch', 'config', 'dynamodb', 'dynamodbstreams'
    # 's3', 's3control', 'secretsmanager', 'ses', 'sns', 'sqs', 'ssm', 'stepfunctions', 'sts', 'support', 'swf', 'transcribe', 'amplify', 'apigatewaymanagementapi', 'apigatewayv2', 'appconfig', 'application-autoscaling', 'appsync', 'athena', 'autoscaling', 'azure', 'backup', 'batch', 'ce', 'cloudfront', 'cloudtrail', 'codecommit', 'cognito-identity', 'cognito-idp', 'docdb', 'ecr', 'ecs', 'efs', 'eks', 'elasticache', 'elasticbeanstalk', 'elb', 'elbv2', 'emr', 'fis', 'glacier', 'glue', 'iot-data', 'iot', 'iotanalytics', 'iotwireless', 'kafka', 'kinesisanalytics', 'kinesisanalyticsv2', 'lakeformation', 'mediastore-data', 'mediastore', 'mq', 'mwaa', 'neptune', 'organizations', 'qldb-session', 'qldb', 'rds-data', 'rds', 'redshift-data', 'sagemaker-runtime', 'sagemaker', 'serverlessrepo', 'servicediscovery', 'sesv2', 'timestream-query', 'timestream-write', 'transfer', 'xray'
    excluded_services = ["acmpca", "eks"] # TODO excluding EKS because it requires a lot of resources
    for item in items:
        item.add_marker(pytest.mark.timeout(5*60))
        for service in available_services:
            if item in deselected_items or item in selected_items:
                continue
            test_class_name = item._nodeid.split("::")[0].split("/")[-1]
            if any([True for x in excluded_services if x in test_class_name]):
                deselected_items.append(item)
            elif (f'test_{service}' in test_class_name or f'test_{service.replace("-","")}' in test_class_name):
                selected_items.append(item)
        if item not in selected_items:
            deselected_items.append(item)

    config.hook.pytest_deselected(items=deselected_items)
    items[:] = selected_items
