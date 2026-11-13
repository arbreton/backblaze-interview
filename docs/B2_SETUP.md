# Backblaze B2 setup — bucket + upload credentials

You need four values for `.env`: **B2_KEY_ID**, **B2_APPLICATION_KEY**, **B2_BUCKET**, and **B2_ENDPOINT**.
It takes about 5 minutes. The first 10 GB of storage are free.

## 1. Create an account
<https://www.backblaze.com/sign-up/cloud-storage>. Choose a region when you sign up (US West, US East or EU Central). You can't change it later, and it determines your endpoint.

## 2. Create a bucket
1. Go to **B2 Cloud Storage → Buckets → Create a Bucket**.
2. **Bucket Unique Name**: something globally unique, for example `interview-backblaze-<yourname>`.
3. **Files in Bucket are**: **Private**. The service uses authenticated requests, so the bucket doesn't need to be public.
4. Default Encryption: *Enable* (SSE-B2). It's free and transparent.
5. Object Lock: Disable.
6. Click **Create a Bucket**.
7. On the bucket card, copy the **Endpoint** (for example `s3.us-west-004.backblazeb2.com`). That's `B2_ENDPOINT`.

> Optional: **Lifecycle Settings → "Keep only the last version of the file"**. When the service attaches the AI description, it rewrites the object in place. The service removes the old version itself, but this setting is a second safeguard.

## 3. Create an application key (least privilege)
1. Go to **Account → Application Keys → Add a New Application Key**.
2. Name: `interview-backblaze-service`.
3. **Allow access to Bucket(s)**: select *only* the bucket you just created. Don't use "All".
4. **Type of Access**: **Read and Write**.
5. Leave "Allow List All Bucket Names" unchecked.
6. Click **Create New Key**. **Copy both values now**, because the application key is shown only once:
   - `keyID` → `B2_KEY_ID`
   - `applicationKey` → `B2_APPLICATION_KEY`

Don't use the *Master Application Key*. It can access everything in the account.

## 4. Fill in `.env`
```bash
cp .env.example .env
# then edit:
B2_KEY_ID=004xxxxxxxxxxxx0000000001
B2_APPLICATION_KEY=K004xxxxxxxxxxxxxxxxxxxxxxxxxxx
B2_BUCKET=interview-backblaze-yourname
B2_ENDPOINT=s3.us-west-004.backblazeb2.com
```

## 5. Verify
```bash
uv run python cli.py --check     # verifies B2 + Claude credentials, uploads nothing
```

## How uploading works (what the service does)
The service uses B2's **S3-compatible API** with `boto3`:

| Step | S3 call | Notes |
|---|---|---|
| Store | `PutObject` (or multipart for large files via `upload_fileobj`) | Metadata: original filename, sha256, uploaded-at |
| Describe | Claude API | Image → caption, text/PDF → summary |
| Attach | `CopyObject` onto itself with `MetadataDirective=REPLACE` | S3/B2 metadata is immutable, so you change it by copying the object in place. B2 does this server-side, so no bytes are re-uploaded |
| Clean up | `DeleteObject(VersionId=<pre-description version>)` | Keeps one version per file |

Metadata appears as `x-amz-meta-ai-description` and related headers (B2 calls them "File Info"). To see it in the B2 web UI, open the file in **Browse Files** and check the file details.

Manual upload with the AWS CLI, for reference:
```bash
aws s3 cp ./photo.jpg s3://$B2_BUCKET/uploads/photo.jpg \
  --endpoint-url https://$B2_ENDPOINT \
  --metadata ai-description="A dog on a beach"
```
