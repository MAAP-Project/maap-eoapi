# Config Tests

This directory contains tests for the MAAP EOAPI configuration module.

## Running Tests

To run the tests, execute the following command from the project root:

```bash
npm test
```

## Test Coverage

The test suite covers the following aspects of the config.ts module:

1. **Basic Configuration Parsing**: Tests that environment variables are correctly parsed into the Config object.

2. **Required Variables**: Tests that the config module correctly throws errors when required environment variables are missing.

3. **JSON Parsing**: Tests the parsing of JSON-formatted environment variables, such as BASTION_HOST_IPV4_ALLOW_LIST.

4. **Error Handling**: Tests graceful handling of malformed JSON in environment variables.

5. **Optional Variables**: Tests that optional environment variables are correctly set when provided.

6. **Stack Name Generation**: Tests the buildStackName helper function.

## Adding New Tests

When adding new configuration options to the Config class, make sure to:

1. Add corresponding tests to ensure the new configuration is parsed correctly.
2. Update the test setup in the beforeEach() function to include default values for any new required environment variables.

## Test Environment

The tests use Jest's mocking capabilities to simulate various environment variable configurations without affecting your actual environment variables.