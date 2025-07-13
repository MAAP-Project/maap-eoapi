# STAC Metadata Generation and Ingestion Infrastructure

## Overview

The MAAP project uses a modern, event-driven serverless architecture for STAC (SpatioTemporal Asset Catalog) metadata generation and ingestion. This infrastructure supports multiple pathways for publishing collections and items to the MAAP STAC catalogs (public and internal).

## Architecture Components

### Core Infrastructure

The STAC infrastructure consists of two main components that work together in a pipeline:

1. **\*ItemGenerator** - A process that generates STAC items and posts them to the StacLoader SNS topic
3. **StacLoader** - Loads STAC objects into the pgstac database

In MAAP, we have two ItemGenerators (one each for the internal and public STACs):

1. **DpsStacItemGenerator** - Listens for catalog.json files uploaded to the DPS output bucket
2. **StactoolsItemGenerator** - Generates STAC items using stactools packages  

### Event-Driven Workflow

All components use SNS + SQS + Lambda for reliable, scalable processing:

- **SNS Topics** serve as event routing hubs
- **SQS Queues** provide buffering and batching capabilities  
- **Lambda Functions** process events with automatic scaling
- **Dead Letter Queues** capture failed processing attempts for debugging

## DPS STAC Integration

DPS jobs that write a catalog.json with some associated STAC items will have the STAC metadata written to a non-public-facing STAC.

### DpsStacItemGenerator (`cdk/constructs/DpsStacItemGenerator/`)

Monitors S3 buckets for DPS (Data Processing System) outputs and automatically generates STAC items:

**Workflow:**

1. DPS job uploads `catalog.json` to job output directory
2. S3 sends event notifications to the DpsStacItemGenerator SNS topic
3. Lambda function processes the S3 event:
   - Extracts the catalog.json S3 path from the event
   - Identifies items that are included in the catalog.json
   - Publishes items to the StacLoader topic under a user- and algorithm-specific collection id

## Stactools-based pipeline for publishing to the MAAP STAC

In general we recommend creating a `stactools` package for any new dataset that is to be published in the MAAP STAC.

### StactoolsItemGenerator (`eoapi-cdk/lib/stactools-item-generator/`)

Generates STAC items using any stactools package via dynamic installation:

**Workflow:**

1. Publish ItemRequest messages to the StactoolsItemGenerator SNS topic
2. Lambda function receives requests and:
   - Installs the specified stactools package using `uvx`
   - Executes `create-item` command with provided arguments
   - Publishes generated STAC items to the StacLoader topic

**Message Format:**

Each StactoolsItemGenerator message contains:

- **package_name**: Package source (PyPI or Git repo)
- **group_name**: CLI command group from the stactools package
- **create_item_args**: Positional arguments for the `create-item` command
- **collection_id**: Target collection for the generated items

```json
{
  "package_name": "stactools-glad-global-forest-change",
  "group_name": "gladglobalforestchange", 
  "create_item_args": ["https://example.com/data.tif"],
  "collection_id": "glad-global-forest-change-1.11"
}
```

## STAC Object Loading

### StacLoader (`eoapi-cdk/lib/stac-loader/`)

Loads STAC collections and items into the pgstac PostgreSQL database:

**Workflow:**

1. Receives STAC objects via SNS topic (from generators or direct publishing)
2. SQS batches messages for efficient processing (up to 500 objects per batch)
3. Lambda function validates and inserts objects into pgstac database
4. Supports both direct STAC object publishing and S3 event notifications

**Key Features:**

- Intelligent batching (by count or time window)
- Automatic collection creation when `CREATE_COLLECTIONS_IF_MISSING=TRUE` (only enabled for the DPS-based STAC)
- Batch failure reporting for partial processing retries
- Dead letter queue for failed ingestion attempts

## Publishing Workflows

### 1. DPS Integration (Automated)

- Configure S3 bucket notifications to send events to DpsStacItemGenerator topic
- DPS uploads catalog.json files → automatic STAC item generation → database insertion

### 2. Stactools-based Generation (Manual/Scripted)  

- Publish ItemRequest messages to StactoolsItemGenerator topic
- Supports any stactools package for flexible data source handling

### 3. Direct Publishing

- Publish STAC JSON directly to StacLoader topic
- Useful for pre-generated STAC objects or custom generation workflows

## Monitoring and Operations

### CloudWatch Integration

- Lambda function logs: `/aws/lambda/{FunctionName}`
- Queue metrics: Message counts, processing rates, failures
- Dead letter queue monitoring for failed processing attempts

### Key Metrics to Monitor

- Queue depth and age of oldest message
- Lambda function duration and error rates
- Dead letter queue message counts
- Database connection and insertion rates

### Troubleshooting

1. Always use `pystac` and/or `stac-pydantic` to validate STAC items and collections **before** posting them to the StacLoader!
2. Check Lambda logs for specific error messages
3. Inspect dead letter queues for failed messages
4. Verify S3 bucket notification configurations
5. Ensure IAM permissions for cross-account access
6. Monitor database connection limits and performance

## Comparison to Legacy Infrastructure (synchronous STAC ingestor API)

We still have a synchronous STAC Ingstor API that provides similar functionality but via an HTTP API and a dynamodb-based queue. This system will still work but for large-scale ingestion operations we recommend using the new event-based infrastructure.

The new infrastructure provides:

- **Better Scalability**: Automatic scaling based on queue depth
- **Improved Reliability**: Dead letter queues and retry mechanisms  
- **Multiple Input Pathways**: S3 events, direct publishing, stactools generation
- **Decoupled Components**: Each component can be scaled independently
- **Batch Processing**: More efficient database operations

## Example: Publishing a New Collection with Stactools

This example demonstrates the complete workflow for publishing the `icesat2-boreal-v3.0` collections to MAAP STAC using the event-driven infrastructure.

### Prerequisites

1. **Assets prepared**: Data files copied to the canonical S3 bucket (`nasa-maap-data-store`)
2. **Stactools package**: Custom stactools package available (e.g., `stactools-icesat2-boreal`)
3. **Infrastructure deployed**: StactoolsItemGenerator and StacLoader components active
4. **AWS access credentials**: To execute this you must be operating in an environment with access credentials for the SMCE AWS account.

### Step 1: Create and Publish Collection

First, generate the STAC collection metadata and publish it directly to the StacLoader:

```python
import boto3
import json
from stactools.icesat2_boreal.stac import create_collection
from stactools.icesat2_boreal.constants import Variable

# Create collection using stactools package
agb_collection = create_collection(Variable.AGB)

# Validate the collection
agb_collection.validate()

# Publish collection to StacLoader SNS topic
sns_client = boto3.client("sns")
STAC_LOADER_SNS_TOPIC_ARN = "arn:aws:sns:us-west-2:916098889494:MAAP-STAC-..."

response = sns_client.publish(
    TopicArn=STAC_LOADER_SNS_TOPIC_ARN, 
    Message=json.dumps(agb_collection.to_dict())
)
```

### Step 2: Generate StactoolsItemGenerator Messages

Create messages that specify the stactools package and arguments for item generation:

```python
# Generate asset pairs (COG and CSV files for each tile)
asset_keys = [
    ("s3://nasa-maap-data-store/file-staging/nasa-map/icesat2-boreal-v3.0/agb/0000001/boreal_agb_tile_0000001.tif",
     "s3://nasa-maap-data-store/file-staging/nasa-map/icesat2-boreal-v3.0/agb/0000001/boreal_agb_tile_0000001_train_data.csv"),
    # ... more asset pairs
]

# Create StactoolsItemGenerator messages
stactools_messages = [
    {
        "package_name": "git+https://github.com/MAAP-Project/icesat2-boreal-stac@0.3.1",
        "group_name": "icesat2boreal",
        "create_item_args": [cog_key, csv_key],
        "collection_id": "icesat2-boreal-v3.0-agb"
    }
    for cog_key, csv_key in asset_keys
]
```

### Step 3: Publish Item Generation Requests

Send the messages to StactoolsItemGenerator in batches:

```python
STACTOOLS_ITEM_GENERATOR_SNS_TOPIC_ARN = "arn:aws:sns:us-west-2:916098889494:MAAP-STAC-..."

def publish_stactools_messages(messages, batch_size=10):
    """Publish messages in batches to avoid rate limits"""
    for i in range(0, len(messages), batch_size):
        batch = messages[i:i + batch_size]
        
        batch_entries = []
        for j, message in enumerate(batch):
            batch_entries.append({
                "Id": f"msg-{i + j:04d}",
                "Message": json.dumps(message)
            })
        
        # Publish batch to SNS
        response = sns_client.publish_batch(
            TopicArn=STACTOOLS_ITEM_GENERATOR_SNS_TOPIC_ARN,
            PublishBatchRequestEntries=batch_entries
        )
        
        print(f"Batch {i//batch_size + 1}: {len(response.get('Successful', []))} successful")

# Publish all item generation requests
publish_stactools_messages(stactools_messages)
```

### Step 4: Monitor Processing

The infrastructure will automatically:

1. **StactoolsItemGenerator** receives the messages:
   - Installs `stactools-icesat2-boreal` package using `uvx`
   - Executes `icesat2boreal create-item <cog_url> <csv_url>` for each message
   - Publishes generated STAC items to StacLoader topic

2. **StacLoader** receives the generated items:
   - Batches items for efficient database insertion
   - Loads items into the pgstac database
   - Reports any failures to dead letter queue

You will need to monitor the SQS queues (including the DeadLetterQueue) to monitor progress, or you can validate success/failure by querying the STAC API.

