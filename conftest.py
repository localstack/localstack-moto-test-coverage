import csv
import json
import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
import boto3 as boto3
import docker as docker
from botocore.config import Config
import pytest as pytest
import requests as requests
from requests.adapters import HTTPAdapter, Retry

from moto import settings
from moto.core.models import BaseMockAWS
from tests import DEFAULT_ACCOUNT_ID

BASE_PATH = os.path.join(os.path.dirname(__file__), "../../target/reports")

FNAME_RAW_DATA_CSV = os.path.join(BASE_PATH,"metric_data_raw.csv")

class Test:
    container_id: str

# overriding ENVs + settings to use LocalStack as endpoint
os.environ["TEST_SERVER_MODE_ENDPOINT"] = 'http://localhost:4566'
os.environ["MOTO_CALL_RESET_API"] = 'false'
settings.TEST_SERVER_MODE = True


# TODO could also use fixture to override endpoint_url
@pytest.fixture(scope="session", autouse=True)
def default_localstack_client_config_fixture() -> Iterator[None]:
    def localstack_client(*args, org=boto3.client, **kwargs):
        # kwargs["endpoint_url"] = 'http://localhost:4566'
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

    with patch("moto.core.models.BaseMockAWS.mock_env_variables", autospec=True,
               side_effect=localstack_mock_env_variables):
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
    payload = {'persistence': True}  # defining no services means all services will be cleared (we don't know which interservice communication tests trigger)
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

    else:
        print("test succeeded")
        node_id = request._pyfuncitem.nodeid
        metric_response = requests.get("http://localhost:4566/metrics/raw")
        metric_json = json.loads(metric_response.content.decode("utf-8"))

        with open(FNAME_RAW_DATA_CSV, "a") as fd:
            writer = csv.writer(fd)
            for m in metric_json.get("metrics"):
                m["node_id"] = node_id
                writer.writerow(m.values())

    url = "http://localhost:4566/metrics/reset"
    r = requests.delete(
        url,
        timeout=90
    )
    assert r.status_code == 200


# probably not required, as we will not need a switch
# def pytest_addoption(parser):
#     group = parser.getgroup("motofallback")
#     group.addoption(
#         "--fallback",
#         action="store_true",
#         help="Run Tests with Moto Fallback for LocalStack",
#     )

def _startup_localstack():
    try:
        _localstack_health_check()
    except:
        import os
        os.system('DNS_ADDRESS=127.0.0.1 EXTENSION_DEV_MODE=1 LOCALSTACK_API_KEY=$LOCALSTACK_API_KEY localstack start -d')

        _localstack_health_check()

    print("LocalStack running")

def _shutdown_localstack():
    import os
    os.system('localstack stop')

def _startup_localstack_docker():
    try:
        client = docker.from_env()
        _localstack_health_check()
    except:
        print("\nStarting LocalStack...")
        localstack_image = "localstack/localstack-pro:latest"
        _docker_service_health(client)
        _pull_docker_image(client, localstack_image)
        _start_docker_container(client, localstack_image)
        _localstack_health_check()
        client.close()


def _shutdown_localstack_docker():
    try:
        if Test.container_id:
            client = docker.from_env()
            print("\nStopping LocalStack...")
            client.containers.get(Test.container_id).stop()
            client.close()
        else:
            print("LocalStack was not started by the test framework")
    except:
        print("LocalStack not running")


# TODO "package" doesn't seem to work, but "module" re-starts LS too often
@pytest.fixture(scope="package", autouse=True)
def startup_localstack():
    _startup_localstack()
    print("LocalStack is ready...")

    yield

    _shutdown_localstack()

@pytest.hookimpl()
def pytest_sessionstart(session: "Session") -> None:
    Path(BASE_PATH).mkdir(parents=True, exist_ok=True)
    with open(FNAME_RAW_DATA_CSV, "w") as fd:
        writer = csv.writer(fd)
        writer.writerow(["service", "operation", "parameters", "response_code", "response_data", "exception", "origin", "node_id"])

# def shutdown_localstack():
#     print("\nStopping LocalStack...")
#     client = docker.from_env()
#     client.containers.get(container_id).stop()
#     client.close()

def pytest_collection_modifyitems(items, config):
    # if config.option.fallback is False:
    #   return

    selected_items = []
    deselected_items = []
    _startup_localstack()
    response = requests.get("http://localhost:4566/_localstack/health").content.decode("utf-8")
    #available_services = [k for k in json.loads(response).get("services").keys()]
    available_services = ['acm']
    #available_services = ['s3', 's3control', 'secretsmanager', 'ses', 'sns', 'sqs', 'ssm', 'stepfunctions', 'sts', 'support', 'swf', 'transcribe', 'amplify', 'apigatewaymanagementapi', 'apigatewayv2', 'appconfig', 'application-autoscaling', 'appsync', 'athena', 'autoscaling', 'azure', 'backup', 'batch', 'ce', 'cloudfront', 'cloudtrail', 'codecommit', 'cognito-identity', 'cognito-idp', 'docdb', 'ecr', 'ecs', 'efs', 'eks', 'elasticache', 'elasticbeanstalk', 'elb', 'elbv2', 'emr', 'fis', 'glacier', 'glue', 'iot-data', 'iot', 'iotanalytics', 'iotwireless', 'kafka', 'kinesisanalytics', 'kinesisanalyticsv2', 'lakeformation', 'mediastore-data', 'mediastore', 'mq', 'mwaa', 'neptune', 'organizations', 'qldb-session', 'qldb', 'rds-data', 'rds', 'redshift-data', 'sagemaker-runtime', 'sagemaker', 'serverlessrepo', 'servicediscovery', 'sesv2', 'timestream-query', 'timestream-write', 'transfer', 'xray']
    # ['ec2', 'es', 'events', 'firehose', 'iam', 'kinesis', 'kms', 'lambda', 'logs', 'opensearch', 'redshift', 'resource-groups', 'resourcegroupstaggingapi', 'route53', 'route53resolver']  # just for initial testing in CI
    # 'acm', 'apigateway', 'cloudformation', 'cloudwatch', 'config', 'dynamodb', 'dynamodbstreams'
    # 's3', 's3control', 'secretsmanager', 'ses', 'sns', 'sqs', 'ssm', 'stepfunctions', 'sts', 'support', 'swf', 'transcribe', 'amplify', 'apigatewaymanagementapi', 'apigatewayv2', 'appconfig', 'application-autoscaling', 'appsync', 'athena', 'autoscaling', 'azure', 'backup', 'batch', 'ce', 'cloudfront', 'cloudtrail', 'codecommit', 'cognito-identity', 'cognito-idp', 'docdb', 'ecr', 'ecs', 'efs', 'eks', 'elasticache', 'elasticbeanstalk', 'elb', 'elbv2', 'emr', 'fis', 'glacier', 'glue', 'iot-data', 'iot', 'iotanalytics', 'iotwireless', 'kafka', 'kinesisanalytics', 'kinesisanalyticsv2', 'lakeformation', 'mediastore-data', 'mediastore', 'mq', 'mwaa', 'neptune', 'organizations', 'qldb-session', 'qldb', 'rds-data', 'rds', 'redshift-data', 'sagemaker-runtime', 'sagemaker', 'serverlessrepo', 'servicediscovery', 'sesv2', 'timestream-query', 'timestream-write', 'transfer', 'xray'
    excluded_services = ["acmpca", "eks"]  # TODO excluding EKS because it requires a lot of resources
    for item in items:
        item.add_marker(pytest.mark.timeout(5 * 60))
        for service in available_services:
            if item in deselected_items or item in selected_items:
                continue
            test_class_name = item._nodeid.split("::")[0].split("/")[-1]
            if any([True for x in excluded_services if x in test_class_name]):
                deselected_items.append(item)
            elif (f'test_{service}' in test_class_name or f'test_{service.replace("-", "")}' in test_class_name):
                selected_items.append(item)
        if item not in selected_items:
            deselected_items.append(item)

    config.hook.pytest_deselected(items=deselected_items)
    items[:] = selected_items


def _docker_service_health(client):
    """Check if the docker service is healthy"""
    if not client.ping():
        print("\nPlease start docker daemon and try again")
        raise Exception("Docker is not running")


def _start_docker_container(client, localstack_image):
    """Start the docker container"""
    # env_vars = ["DEBUG=1", "PROVIDER_OVERRIDE_S3=asf", "FAIL_FAST=1"]
    env_vars = []
    if os.environ.get("LOCALSTACK_API_KEY"):
        env_vars.append(f"LOCALSTACK_API_KEY={os.environ.get('LOCALSTACK_API_KEY')}")
        env_vars.append("EXTENSION_DEV_MODE=1")
        print("Trying to start LocalStack Pro")
    else:
        print("No LOCALSTACK_API_KEY found, running community...")
    port_mappings = {
        "53/tcp": ("127.0.0.1", 53),
        "53/udp": ("127.0.0.1", 53),
        "443": ("127.0.0.1", 443),
        "4566": ("127.0.0.1", 4566),
        "4571": ("127.0.0.1", 4571),
    }
    volumes = ["/var/run/docker.sock:/var/run/docker.sock"]
    localstack_container = client.containers.run(
        image=localstack_image,
        detach=True,
        ports=port_mappings,
        name="localstack_main",
        volumes=volumes,
        auto_remove=True,
        environment=env_vars,
    )
    Test.container_id = localstack_container.id


def _localstack_health_check():
    """Check if the localstack service is healthy"""
    localstack_health_url = "http://localhost:4566/_localstack/health"
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=2)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.get(localstack_health_url)
    session.close()


def _pull_docker_image(client, localstack_image):
    """Pull the docker image"""
    docker_image_list = client.images.list(name=localstack_image)
    if len(docker_image_list) == 0:
        print(f"Pulling image {localstack_image}")
        client.images.pull(localstack_image)
    docker_image_list = client.images.list(name=localstack_image)
    print(f"Using LocalStack image: {docker_image_list[0].id}")
