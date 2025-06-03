"""AWS Lambda handler for STAC Item Generation."""

import json
import logging
import os
from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Optional, TypedDict

import boto3
from pydantic import ValidationError

from dps_stac_item_generator.item import get_stac_items

if TYPE_CHECKING:
    from aws_lambda_typing.context import Context
else:
    Context = Annotated[object, "Context object"]


logger = logging.getLogger()
if logger.hasHandlers():
    logger.handlers.clear()

log_handler = logging.StreamHandler()

log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level = logging._nameToLevel.get(log_level_name, logging.INFO)
logger.setLevel(log_level)

formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)


def get_topic_arn() -> str:
    item_load_topic_arn = os.environ.get("ITEM_LOAD_TOPIC_ARN")
    if not item_load_topic_arn:
        logger.error("Environment variable ITEM_LOAD_TOPIC_ARN is not set.")
        raise EnvironmentError("ITEM_LOAD_TOPIC_ARN must be set")

    return item_load_topic_arn


def get_catalog_json_key(message_str: str) -> str:
    """Process an S3 event notification and return STAC item data."""
    try:
        message_data = json.loads(message_str)
        records: List[Dict[str, Any]] = message_data.get("Records", [])
        if not records:
            raise ValueError("no S3 event records!")
        elif len(records) > 1:
            raise ValueError("more than one S3 event record!")

        s3_data = records[0]["s3"]
        bucket_name = s3_data["bucket"]["name"]
        object_key = s3_data["object"]["key"]

        # Validate that this looks like a STAC item file
        if not object_key.endswith("catalog.json"):
            raise ValueError(
                f"S3 object key does not appear to be a catalog.json: {object_key}"
            )

        return f"s3://{bucket_name}/{object_key}"

    except KeyError as e:
        logger.error(f"S3 event missing required field: {e}")
        raise ValueError(f"Invalid S3 event structure: missing {e}") from e
    except Exception as e:
        logger.error(f"Failed to process S3 event: {e}")
        raise


class BatchItemFailure(TypedDict):
    itemIdentifier: str


class PartialBatchFailureResponse(TypedDict):
    batchItemFailures: List[BatchItemFailure]


def handler(
    event: Dict[str, Any], context: Context
) -> Optional[PartialBatchFailureResponse]:
    """
    AWS Lambda handler function triggered by SQS with batching enabled.

    Processes messages in batches, attempts to generate STAC items, publishes
    successful results to SNS, and reports partial batch failures to SQS.
    """
    records = event.get("Records", [])
    aws_request_id = getattr(context, "aws_request_id", "N/A")
    remaining_time = getattr(context, "get_remaining_time_in_millis", lambda: "N/A")()

    try:
        sns_client = boto3.client("sns", region_name=os.getenv("AWS_DEFAULT_REGION"))
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        raise EnvironmentError("AWS_DEFAULT_REGION must be set") from e

    logger.info(f"Received batch with {len(records)} records.")
    logger.debug(
        f"Lambda Context: RequestId={aws_request_id}, RemainingTime={remaining_time}ms"
    )

    batch_item_failures: List[BatchItemFailure] = []

    for record in records:
        message_id = record.get("messageId")
        if not message_id:
            logger.warning("Record missing messageId, cannot report failure for it.")
            continue

        try:
            sqs_body_str = record["body"]
            logger.debug(f"[{message_id}] SQS message body: {sqs_body_str}")
            sns_notification = json.loads(sqs_body_str)

            message_str = sns_notification["Message"]
            logger.debug(f"[{message_id}] SNS Message content: {message_str}")

            catalog_json_key = get_catalog_json_key(message_str)
            for stac_item in get_stac_items(catalog_json_key):
                stac_item_json = stac_item.model_dump_json()

                item_load_topic_arn = get_topic_arn()
                logger.info(
                    f"[{message_id}] Publishing STAC item {stac_item.id} to {item_load_topic_arn}"
                )
                response = sns_client.publish(
                    TopicArn=item_load_topic_arn,
                    Message=stac_item_json,
                )
                logger.info(
                    f"[{message_id}] SNS publish response MessageId: {response.get('MessageId')}"
                )

            logger.debug(f"[{message_id}] Successfully processed.")

        except (ValueError, KeyError, ValidationError, json.JSONDecodeError) as e:
            logger.error(f"[{message_id}] Failed with error: {e}", extra=record)
            batch_item_failures.append({"itemIdentifier": message_id})
        except Exception as e:
            logger.error(f"[{message_id}] Unexpected error: {e}", extra=record)
            batch_item_failures.append({"itemIdentifier": message_id})

    if batch_item_failures:
        logger.warning(
            f"Finished processing batch. {len(batch_item_failures)} failure(s) reported."
        )
        logger.info(
            f"Returning failed item identifiers: {[f['itemIdentifier'] for f in batch_item_failures]}"
        )
        return {"batchItemFailures": batch_item_failures}
    else:
        logger.info("Finished processing batch. All records successful.")
        return None
