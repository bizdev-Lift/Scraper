import logging
import os

import boto3
from botocore.exceptions import NoCredentialsError

logger = logging.getLogger(__name__)


class S3Client:
    """
    A client for interacting with Amazon S3.
    """

    def __init__(self, bucket_name: str = None):
        """
        Initializes the S3 client.

        Args:
            bucket_name (str, optional): The name of the S3 bucket.
                                         If not provided, it will try to get it from the
                                         'AWS_BUCKET_NAME' environment variable.

        Raises:
            ValueError: If the bucket name is not provided or found in the environment variables.
        """
        self.bucket_name = bucket_name or os.environ.get("AWS_BUCKET_NAME")
        if not self.bucket_name:
            raise ValueError(
                "S3 bucket name must be provided or set as AWS_BUCKET_NAME environment variable."
            )

        self.s3 = boto3.client("s3")

    def write_raw_content(self, key: str, content: str) -> bool:
        """
        Writes string content to a specified S3 key.

        Args:
            key (str): The key (path/filename) for the object in S3.
            content (str): The string content to upload.

        Returns:
            bool: True if the upload is successful, False otherwise.
        """
        try:
            self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content.encode("utf-8"))
            logger.info(f"Successfully wrote content to s3://{self.bucket_name}/{key}")
            return True
        except NoCredentialsError:
            logger.error("AWS credentials not found. Please configure your credentials.")
            return False
        except Exception as e:
            logger.error(f"Failed to write to S3: {e}")
            return False

    def read_content(self, key: str) -> str | None:
        """
        Reads content from a specified S3 key.

        Args:
            key (str): The key (path/filename) for the object in S3.

        Returns:
            str | None: The content of the file as a string, or None if an error occurs.
        """
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            content = response["Body"].read().decode("utf-8")
            logger.info(f"Successfully read content from s3://{self.bucket_name}/{key}")
            return content
        except NoCredentialsError:
            logger.error("AWS credentials not found. Please configure your credentials.")
            return None
        except self.s3.exceptions.NoSuchKey:
            logger.warning(f"File not found at s3://{self.bucket_name}/{key}")
            return None
        except Exception as e:
            logger.error(f"Failed to read from S3: {e}")
            return None

    def list_objects(self, prefix: str) -> list[str]:
        """
        Lists object keys under a given prefix.

        Args:
            prefix (str): The prefix to filter objects by.

        Returns:
            list[str]: A list of object keys, or an empty list if none found.
        """
        try:
            response = self.s3.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)
            return [obj["Key"] for obj in response.get("Contents", [])]
        except Exception as e:
            logger.error(f"Failed to list objects under {prefix}: {e}")
            return []

    def delete_objects(self, keys: list[str]) -> bool:
        """
        Deletes a list of objects from the bucket.

        Args:
            keys (list[str]): The list of object keys to delete.

        Returns:
            bool: True if successful, False otherwise.
        """
        if not keys:
            return True
        try:
            self.s3.delete_objects(
                Bucket=self.bucket_name,
                Delete={"Objects": [{"Key": key} for key in keys]},
            )
            logger.info(f"Deleted {len(keys)} objects from s3://{self.bucket_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete objects from S3: {e}")
            return False
