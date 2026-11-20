"""Just-in-time AWS access for AI agents."""

import logging

__version__ = "0.1.0"

# Debug mode raises the Lambda log level, which also turns on very chatty AWS SDK and HTTP logs.
# Keep those at WARNING so only this project's debug lines show.
for _noisy in ("boto3", "botocore", "urllib3", "s3transfer"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
