import json
import logging
import os
from unittest.mock import patch

import pytest
from dps_stac_item_generator import handler as item_gen_handler
from stac_pydantic.item import Item


@pytest.fixture(autouse=True)
def setup_environment(monkeypatch):
    """Set necessary environment variables for tests."""
    monkeypatch.setenv(
        "ITEM_LOAD_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:fake-topic"
    )
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def mock_context(mocker):
    """Create a mock Lambda context object."""
    mock_ctx = mocker.MagicMock()
    mock_ctx.aws_request_id = "test-request-id"
    mock_ctx.get_remaining_time_in_millis.return_value = 300000  # 5 minutes
    return mock_ctx


@pytest.fixture
def mock_sns_client(mocker):
    """Mock the boto3 SNS client and its publish method."""
    mock_client_instance = mocker.MagicMock()
    mock_client_instance.publish.return_value = {"MessageId": "fake-sns-message-id"}

    mock_boto_client = patch(
        "dps_stac_item_generator.handler.boto3.client",
        return_value=mock_client_instance,
    ).start()

    yield mock_client_instance

    mock_boto_client.stop()


@pytest.fixture
def mock_get_stac_items(mocker):
    """Mock the get_stac_items function."""
    mock_item_dict = {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "test_item_id",
        "properties": {
            "datetime": "2023-01-01T00:00:00Z",
        },
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "links": [],
        "assets": {},
        "bbox": [0, 0, 0, 0],
        "stac_extensions": [],
        "collection": "test_collection",
    }
    mock_item = Item(**mock_item_dict)

    mock_func = patch(
        "dps_stac_item_generator.handler.get_stac_items", return_value=[mock_item]
    ).start()

    mock_func.mock_item = mock_item
    mock_func.mock_item_dict = mock_item_dict
    mock_func.mock_item_json = mock_item.model_dump_json()

    yield mock_func

    mock_func.stop()


def create_sqs_event_with_s3_notification(s3_events: list[dict]) -> dict:
    """Helper function to create an SQS event structure with S3 notifications."""
    records = []
    for i, s3_event_data in enumerate(s3_events):
        s3_notification = {
            "Records": [
                {
                    "eventVersion": "2.1",
                    "eventSource": "aws:s3",
                    "awsRegion": "us-east-1",
                    "eventTime": "2023-01-01T12:00:00.000Z",
                    "eventName": "ObjectCreated:Put",
                    "s3": s3_event_data,
                }
            ]
        }

        sns_message_str = json.dumps(s3_notification)
        sns_notification = {
            "Type": "Notification",
            "MessageId": f"sns-msg-id-{i}",
            "TopicArn": "arn:aws:sns:us-east-1:123456789012:s3-events-topic",
            "Subject": "Amazon S3 Notification",
            "Message": sns_message_str,
            "Timestamp": "2023-01-01T12:00:00.000Z",
            "SignatureVersion": "1",
        }

        sqs_body_str = json.dumps(sns_notification)
        records.append(
            {
                "messageId": f"sqs-msg-id-{i}",
                "receiptHandle": f"receipt-handle-{i}",
                "body": sqs_body_str,
                "attributes": {
                    "ApproximateReceiveCount": "1",
                    "SentTimestamp": "1672574400000",
                    "SenderId": "ARO...",
                    "ApproximateFirstReceiveTimestamp": "1672574400010",
                },
                "messageAttributes": {},
                "md5OfBody": f"md5-{i}",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:catalog-events-queue",
                "awsRegion": "us-east-1",
            }
        )
    return {"Records": records}


def test_handler_success_single_message(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test successful processing of a single valid SQS message with S3 catalog.json event."""
    caplog.set_level(logging.INFO)
    s3_event_data = {
        "bucket": {"name": "test-catalog-bucket"},
        "object": {"key": "path/to/catalog.json"},
    }
    event = create_sqs_event_with_s3_notification([s3_event_data])

    expected_s3_uri = "s3://test-catalog-bucket/path/to/catalog.json"

    result = item_gen_handler.handler(event, mock_context)

    assert result is None

    mock_get_stac_items.assert_called_once_with(expected_s3_uri)

    mock_sns_client.publish.assert_called_once_with(
        TopicArn=os.environ["ITEM_LOAD_TOPIC_ARN"],
        Message=mock_get_stac_items.mock_item_json,
    )

    assert "Received batch with 1 records." in caplog.text
    assert f"Publishing STAC item {mock_get_stac_items.mock_item.id}" in caplog.text
    assert "SNS publish response MessageId: fake-sns-message-id" in caplog.text
    assert "Successfully processed." in caplog.text
    assert "Finished processing batch. All records successful." in caplog.text


def test_handler_success_multiple_messages(
    mock_context, mock_sns_client, mock_get_stac_items, mocker, caplog
):
    """Test successful processing of multiple valid SQS messages with S3 catalog.json events."""
    s3_event_data1 = {
        "bucket": {"name": "test-catalog-bucket-1"},
        "object": {"key": "path1/catalog.json"},
    }
    s3_event_data2 = {
        "bucket": {"name": "test-catalog-bucket-2"},
        "object": {"key": "path2/catalog.json"},
    }
    event = create_sqs_event_with_s3_notification([s3_event_data1, s3_event_data2])

    item1_dict = {**mock_get_stac_items.mock_item_dict, "id": "item1"}
    item2_dict = {**mock_get_stac_items.mock_item_dict, "id": "item2"}

    item1 = Item(**item1_dict)
    item2 = Item(**item2_dict)

    item1_json = item1.model_dump_json()
    item2_json = item2.model_dump_json()

    mock_get_stac_items.side_effect = [[item1], [item2]]

    result = item_gen_handler.handler(event, mock_context)

    assert result is None
    assert mock_get_stac_items.call_count == 2
    assert mock_sns_client.publish.call_count == 2

    expected_calls = [
        mocker.call("s3://test-catalog-bucket-1/path1/catalog.json"),
        mocker.call("s3://test-catalog-bucket-2/path2/catalog.json"),
    ]
    mock_get_stac_items.assert_has_calls(expected_calls)

    assert mock_sns_client.publish.call_args_list[0] == mocker.call(
        TopicArn=os.environ["ITEM_LOAD_TOPIC_ARN"], Message=item1_json
    )
    assert mock_sns_client.publish.call_args_list[1] == mocker.call(
        TopicArn=os.environ["ITEM_LOAD_TOPIC_ARN"], Message=item2_json
    )

    assert "Successfully processed." in caplog.text
    assert caplog.text.count("Successfully processed.") == 2
    assert "Finished processing batch. All records successful." in caplog.text


def test_handler_partial_failure_get_stac_items(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test partial batch failure when get_stac_items raises an error."""
    s3_event_data_ok = {
        "bucket": {"name": "test-catalog-bucket-ok"},
        "object": {"key": "ok/catalog.json"},
    }
    s3_event_data_fail = {
        "bucket": {"name": "test-catalog-bucket-fail"},
        "object": {"key": "fail/catalog.json"},
    }
    event = create_sqs_event_with_s3_notification(
        [s3_event_data_ok, s3_event_data_fail]
    )

    mock_item_ok_dict = {
        **mock_get_stac_items.mock_item_dict,
        "id": "item_ok",
    }
    mock_item_ok = Item(**mock_item_ok_dict)
    mock_item_ok_json = mock_item_ok.model_dump_json()

    mock_exception = ValueError("Failed to generate STAC items from catalog")

    mock_get_stac_items.side_effect = [[mock_item_ok], mock_exception]

    result = item_gen_handler.handler(event, mock_context)

    expected_failures = [{"itemIdentifier": event["Records"][1]["messageId"]}]
    assert result == {"batchItemFailures": expected_failures}

    assert mock_get_stac_items.call_count == 2
    mock_sns_client.publish.assert_called_once_with(
        TopicArn=os.environ["ITEM_LOAD_TOPIC_ARN"], Message=mock_item_ok_json
    )

    assert (
        f"[{event['Records'][0]['messageId']}] Successfully processed." in caplog.text
    )
    assert (
        f"[{event['Records'][1]['messageId']}] Failed with error: Failed to generate STAC items from catalog"
        in caplog.text
    )
    assert "Finished processing batch. 1 failure(s) reported." in caplog.text


def test_handler_partial_failure_json_decode(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test partial batch failure when JSON decoding fails."""
    s3_event_data_ok = {
        "bucket": {"name": "test-catalog-bucket-ok"},
        "object": {"key": "ok/catalog.json"},
    }
    invalid_json_body = '{"Message": "{"Records": [{"s3": {"bucket": {"name": "test"}, "object": {"key": "catalog.json"}}]", "Type": "Notification"}'

    event = create_sqs_event_with_s3_notification([s3_event_data_ok])
    malformed_record = {
        "messageId": "sqs-msg-id-malformed",
        "receiptHandle": "receipt-handle-malformed",
        "body": invalid_json_body,
        "attributes": {},
        "messageAttributes": {},
        "md5OfBody": "md5-malformed",
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:catalog-events-queue",
        "awsRegion": "us-east-1",
    }
    event["Records"].append(malformed_record)

    mock_item_ok_dict = {
        **mock_get_stac_items.mock_item_dict,
        "id": "item_ok",
    }
    mock_item_ok = Item(**mock_item_ok_dict)
    mock_item_ok_json = mock_item_ok.model_dump_json()
    mock_get_stac_items.return_value = [mock_item_ok]

    result = item_gen_handler.handler(event, mock_context)

    expected_failures = [{"itemIdentifier": malformed_record["messageId"]}]
    assert result == {"batchItemFailures": expected_failures}

    mock_get_stac_items.assert_called_once()
    mock_sns_client.publish.assert_called_once_with(
        TopicArn=os.environ["ITEM_LOAD_TOPIC_ARN"], Message=mock_item_ok_json
    )

    assert f"[{malformed_record['messageId']}] Failed with error:" in caplog.text


def test_handler_partial_failure_invalid_s3_key(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test partial batch failure when S3 object key is not catalog.json."""
    s3_event_data_ok = {
        "bucket": {"name": "test-catalog-bucket-ok"},
        "object": {"key": "ok/catalog.json"},
    }
    s3_event_data_invalid = {
        "bucket": {"name": "test-catalog-bucket-invalid"},
        "object": {"key": "invalid/catalog-not.json"},
    }
    event = create_sqs_event_with_s3_notification(
        [s3_event_data_ok, s3_event_data_invalid]
    )

    mock_item_ok_dict = {**mock_get_stac_items.mock_item_dict, "id": "item_ok"}
    mock_item_ok = Item(**mock_item_ok_dict)
    mock_item_ok_json = mock_item_ok.model_dump_json()
    mock_get_stac_items.return_value = [mock_item_ok]

    result = item_gen_handler.handler(event, mock_context)

    expected_failures = [{"itemIdentifier": event["Records"][1]["messageId"]}]
    assert result == {"batchItemFailures": expected_failures}

    mock_get_stac_items.assert_called_once()
    mock_sns_client.publish.assert_called_once_with(
        TopicArn=os.environ["ITEM_LOAD_TOPIC_ARN"], Message=mock_item_ok_json
    )

    assert (
        f"[{event['Records'][1]['messageId']}] Failed with error: S3 object key does not appear to be a catalog.json: invalid/catalog-not.json"
        in caplog.text
    )


def test_handler_all_records_fail(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test when all records in a batch fail."""
    s3_event_data_1 = {
        "bucket": {"name": "test-catalog-bucket-1"},
        "object": {"key": "file1.tif"},  # not a catalog.json
    }
    s3_event_data_2 = {
        "bucket": {"name": "test-catalog-bucket-2"},
        "object": {"key": "file2.json"},  # not a catalog.json
    }
    event = create_sqs_event_with_s3_notification([s3_event_data_1, s3_event_data_2])

    result = item_gen_handler.handler(event, mock_context)

    expected_failures = [
        {"itemIdentifier": event["Records"][0]["messageId"]},
        {"itemIdentifier": event["Records"][1]["messageId"]},
    ]
    assert result == {"batchItemFailures": expected_failures}

    mock_get_stac_items.assert_not_called()
    mock_sns_client.publish.assert_not_called()

    assert "Finished processing batch. 2 failure(s) reported." in caplog.text


def test_handler_empty_batch(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test handling an empty batch of records."""
    event = {"Records": []}

    result = item_gen_handler.handler(event, mock_context)

    assert result is None
    mock_get_stac_items.assert_not_called()
    mock_sns_client.publish.assert_not_called()
    assert "Received batch with 0 records." in caplog.text
    assert "Finished processing batch. All records successful." in caplog.text


def test_handler_with_general_exception(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test handling of unexpected exceptions during processing."""
    s3_event_data = {
        "bucket": {"name": "test-catalog-bucket"},
        "object": {"key": "path/catalog.json"},
    }
    event = create_sqs_event_with_s3_notification([s3_event_data])
    message_id = event["Records"][0]["messageId"]

    mock_get_stac_items.side_effect = Exception("Unexpected error during processing")

    result = item_gen_handler.handler(event, mock_context)

    expected_failures = [{"itemIdentifier": message_id}]
    assert result == {"batchItemFailures": expected_failures}

    assert (
        f"[{message_id}] Unexpected error: Unexpected error during processing"
        in caplog.text
    )
    assert "Unexpected error" in caplog.text


def test_handler_sns_publish_failure(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test handling of SNS publish failures."""
    s3_event_data = {
        "bucket": {"name": "test-catalog-bucket"},
        "object": {"key": "path/catalog.json"},
    }
    event = create_sqs_event_with_s3_notification([s3_event_data])

    mock_sns_client.publish.side_effect = Exception("SNS publish failed")

    result = item_gen_handler.handler(event, mock_context)

    expected_failures = [{"itemIdentifier": event["Records"][0]["messageId"]}]
    assert result == {"batchItemFailures": expected_failures}

    assert "SNS publish failed" in caplog.text
    assert (
        f"[{event['Records'][0]['messageId']}] Unexpected error: SNS publish failed"
        in caplog.text
    )


def test_handler_multiple_items_from_catalog(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test processing when get_stac_items returns multiple items from a single catalog."""
    s3_event_data = {
        "bucket": {"name": "test-catalog-bucket"},
        "object": {"key": "path/catalog.json"},
    }
    event = create_sqs_event_with_s3_notification([s3_event_data])

    item1_dict = {**mock_get_stac_items.mock_item_dict, "id": "item1"}
    item2_dict = {**mock_get_stac_items.mock_item_dict, "id": "item2"}
    item3_dict = {**mock_get_stac_items.mock_item_dict, "id": "item3"}

    item1 = Item(**item1_dict)
    item2 = Item(**item2_dict)
    item3 = Item(**item3_dict)

    mock_get_stac_items.return_value = [item1, item2, item3]

    result = item_gen_handler.handler(event, mock_context)

    assert result is None
    assert mock_get_stac_items.call_count == 1
    assert mock_sns_client.publish.call_count == 3

    expected_calls = [
        {
            "TopicArn": os.environ["ITEM_LOAD_TOPIC_ARN"],
            "Message": item1.model_dump_json(),
        },
        {
            "TopicArn": os.environ["ITEM_LOAD_TOPIC_ARN"],
            "Message": item2.model_dump_json(),
        },
        {
            "TopicArn": os.environ["ITEM_LOAD_TOPIC_ARN"],
            "Message": item3.model_dump_json(),
        },
    ]

    for i, expected_call in enumerate(expected_calls):
        actual_call = mock_sns_client.publish.call_args_list[i]
        assert actual_call.kwargs == expected_call

    assert "Publishing STAC item item1" in caplog.text
    assert "Publishing STAC item item2" in caplog.text
    assert "Publishing STAC item item3" in caplog.text


def test_handler_missing_s3_fields(
    mock_context, mock_sns_client, mock_get_stac_items, caplog
):
    """Test handling when S3 event is missing required fields."""
    incomplete_s3_event = {
        "Records": [
            {
                "eventVersion": "2.1",
                "eventSource": "aws:s3",
                "awsRegion": "us-east-1",
                "eventTime": "2023-01-01T12:00:00.000Z",
                "eventName": "ObjectCreated:Put",
                "s3": {"object": {"key": "path/catalog.json"}},
            }
        ]
    }

    sns_message_str = json.dumps(incomplete_s3_event)
    sns_notification = {
        "Type": "Notification",
        "MessageId": "sns-msg-id-0",
        "TopicArn": "arn:aws:sns:us-east-1:123456789012:s3-events-topic",
        "Subject": "Amazon S3 Notification",
        "Message": sns_message_str,
        "Timestamp": "2023-01-01T12:00:00.000Z",
        "SignatureVersion": "1",
    }

    sqs_body_str = json.dumps(sns_notification)
    event = {
        "Records": [
            {
                "messageId": "sqs-msg-id-0",
                "receiptHandle": "receipt-handle-0",
                "body": sqs_body_str,
                "attributes": {},
                "messageAttributes": {},
                "md5OfBody": "md5-0",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:catalog-events-queue",
                "awsRegion": "us-east-1",
            }
        ]
    }

    result = item_gen_handler.handler(event, mock_context)

    expected_failures = [{"itemIdentifier": event["Records"][0]["messageId"]}]
    assert result == {"batchItemFailures": expected_failures}

    mock_get_stac_items.assert_not_called()
    mock_sns_client.publish.assert_not_called()

    assert f"[{event['Records'][0]['messageId']}] Failed with error:" in caplog.text
