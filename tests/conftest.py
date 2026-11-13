import io

import boto3
import pytest
from moto import mock_aws

from app.config import Settings
from app.storage import B2Storage


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        b2_key_id="test",
        b2_application_key="test",
        b2_bucket="test-bucket",
        b2_endpoint="",
        b2_prefix="uploads/",
        anthropic_api_key="sk-ant-test",
        local_path_root=str(tmp_path),
        max_upload_mb=1,
    )


@pytest.fixture
def storage(settings):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=settings.b2_bucket)
        # B2 buckets are always versioned; mirror that.
        client.put_bucket_versioning(
            Bucket=settings.b2_bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        yield B2Storage(settings, client=client)


@pytest.fixture
def png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()
