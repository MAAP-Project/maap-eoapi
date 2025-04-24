# MAAP STAC Metadata Generation and Ingestion User Stories

This document captures the user stories for all use cases of the MAAP STAC metadata generation and ingestion pipeline (public and user-generated STACs).

**Goal:** enumerate all common use-cases so that we can confidently design modular infrastructure that can serve all use-cases.

## Use Case 1: DPS Job Outputs Cataloging in User-STAC

### User Story 1A: Single DPS Job Output Cataloging

**As a** researcher running a data processing job,  
**I want to** have the outputs of my DPS job automatically cataloged in the user-STAC,  
**So that** I can easily discover, access, visualize, and share my processed data products.

**Acceptance Criteria:**

- I can specify a collection ID (new or existing) for my job outputs
- If I don't specify a collection, items are added to my default username-based collection
- I control which outputs get indexed by generating a simple JSON with asset key/href pairs (e.g., `{"data": "output.tif", "log": "log.txt"}`)
- I don't need to understand STAC metadata structure to catalog my outputs
- I can easily create the required information from Python or R
- The system automatically generates all required STAC metadata from my minimal input
- Output files are accessible via STAC API within minutes of job completion
- Proper attribution and provenance information is included in the metadata

### User Story 1B: Batch DPS Job Output Cataloging

**As a** researcher running multiple related DPS jobs,  
**I want to** catalog outputs from thousands of related jobs under a single collection,  
**So that** I can easily discover, access, visualize, and share my processed data products.

**Acceptance Criteria:**

- All jobs can reference the same collection ID
- If no collection is specified, all items flow into my default username-based collection
- I control which outputs get indexed by generating a simple JSON with asset key/href pairs for each job
- The system handles all complex STAC metadata generation without requiring me to be a STAC expert
- The system can handle high-volume concurrent cataloging requests
- The collection maintains integrity across thousands of added items
- I can track ingestion progress for the entire batch of jobs
- I can query subsets of my collection based on job-specific metadata

## Use Case 2: Existing File Cataloging in User-STAC

### User Story 2A: Manual File List Cataloging

**As a** user with existing data files,  
**I want to** catalog a specific list of files in the user-STAC,  
**So that** I can make my pre-existing data discoverable and accessible.

**Acceptance Criteria:**

- I can create a new collection for my existing files
- If I don't specify a collection, items are added to my default username-based collection
- I control which files get indexed by providing a simple JSON with asset key/href pairs
- I don't need to understand complex STAC metadata structures
- I can provide a list of file locations (S3 URIs, etc.)
- The system automatically generates appropriate metadata for each file
- I can review and modify the generated metadata if needed
- The cataloged files appear in the user-STAC upon completion

### User Story 2B: Bulk File Discovery and Cataloging

**As a** user with a large dataset in cloud storage,  
**I want to** discover and catalog all files matching certain patterns,  
**So that** I can efficiently manage large collections without manual listing.

**Acceptance Criteria:**

- I can specify file discovery parameters (prefixes, patterns, etc.)
- I can target a specific collection or use my default username-based collection
- I can define simple rules for generating asset key/href pairs for discovered files
- I don't need to understand complex STAC metadata structures
- The system handles the file discovery process
- I can preview discovered files before cataloging
- The system automatically generates appropriate metadata for all discovered files
- I can provide basic configuration for metadata generation without needing STAC expertise
- I can monitor the progress of the bulk cataloging process

## Use Case 3: Publishing to the Public-STAC

### User Story 3A: Standard Dataset Publication by Data Team

**As a** MAAP data team member with administrative privileges,  
**I want to** publish a new standard dataset to the public-STAC,  
**So that** all MAAP users can discover and use authoritative datasets.

**Acceptance Criteria:**

- Only authorized data team members can publish to the public-STAC
- I can create a collection using a published `stactools` package
- I can specify the location of cloud-optimized assets

### User Story 3B: Dataset Updates by Data Team

**As a** MAAP data team member with administrative privileges,  
**I want to** update an existing collection in the public-STAC,  
**So that** users have access to the latest data and metadata.

**Acceptance Criteria:**

- Only authorized data team members can update public-STAC collections
- I can add new items to an existing collection
- I can update collection-level metadata
- I can update individual item metadata when needed
- Update operations preserve existing relationships
- Users can see when collections were last updated

## Cross-Cutting User Stories

### User Story CC1: Metadata Generation Configuration

**As a** user,  
**I want to** configure how STAC metadata is generated for my assets,  
**So that** the metadata accurately represents my data's characteristics.

**Acceptance Criteria:**

- I can select a stactools package for metadata generation
- I can provide custom parameters to the metadata generation process
- I can preview generated metadata before ingestion
- I can save and reuse metadata generation configurations

### User Story CC2: Ingestion Monitoring

**As a** user who has submitted data for cataloging,  
**I want to** monitor the progress and status of my ingestion jobs,  
**So that** I can troubleshoot issues and verify completion.

**Acceptance Criteria:**

- I can see the current status of all my ingestion jobs
- I receive notifications about job completion or failures
- I can access detailed logs for troubleshooting
- I can retry failed ingestions with modified parameters

### User Story CC3: Authentication and Authorization

**As a** MAAP administrator,  
**I want to** control who can ingest data into which collections,  
**So that** data integrity and security are maintained.

**Acceptance Criteria:**

- Users are authenticated before accessing ingestion capabilities
- Access to public-STAC ingestion is restricted to authorized data team members only
- Users can only modify their own collections in user-STAC through ownership verification
- All collections include an `owner` field in their properties to enforce access control
- Users cannot modify or add items to collections they do not own
- All ingestion actions are logged with user attribution

### User Story CC4: Collection Ownership

**As a** user creating a collection in the user-STAC,  
**I want to** have exclusive write access to my collection,  
**So that** I can maintain control over the data and metadata I publish.

**Acceptance Criteria:**

- Each collection has a mandatory `owner` field linked to my user identity
- The system verifies ownership before allowing modifications to a collection
- Other users can discover and read my collections but cannot write to them
- I can view a list of all collections I own
- I can transfer ownership of a collection if needed (future enhancement)

### User Story CC5: Default Collection Management

**As a** user of the MAAP platform,  
**I want to** have a default collection created for me automatically,  
**So that** I can immediately start cataloging items without manual collection setup.

**Acceptance Criteria:**

- The system automatically creates a default collection for each user based on their username
- All my items flow into this default collection if I don't specify another collection
- My default collection uses standard metadata schemas and has me set as the owner
- I can discover and access my default collection through the STAC API

### User Story CC6: Simplified Metadata Generation for Non-STAC Experts

**As a** user with limited knowledge of STAC standards,  
**I want to** easily catalog my data with minimal technical overhead,  
**So that** I can make my data discoverable without needing to become a STAC expert.

**Acceptance Criteria:**

- I can generate metadata by providing just a simple JSON with asset key/href pairs (e.g., `{"data": "output.tif", "log": "log.txt"}`)
- The system handles all the complex STAC metadata generation automatically
- I don't need to understand STAC schemas, extensions, or validation rules
- The system provides clear, non-technical error messages if my input is invalid
- I can start with minimal metadata and learn more advanced options gradually
- The system offers helpful examples and templates for common use cases
- For advanced users, the option to provide more detailed STAC metadata is still available
