# Uploaded files on Cloudflare R2

By default uploads are written to two local folders: `MEDIA_ROOT` (public files) and
`PROTECTED_MEDIA_ROOT` (restricted files). On Render the disk is lost on every deploy, so
production should keep uploads on Cloudflare R2.

## Switch it on

1. In Cloudflare, go to **R2** and create two buckets, for example `nbte-public` and `nbte-private`.
2. Connect a custom domain to the public bucket only, for example `files.nbte.gov.ng`.
   Leave the private bucket without a public URL.
3. Create an R2 API token with "Object Read & Write" on both buckets.
4. Set these environment variables on the web service and on the worker:

| Variable | Example |
|---|---|
| `FILE_STORAGE` | `r2` |
| `R2_ACCOUNT_ID` | the account ID shown on the R2 overview page |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | from the API token |
| `R2_PUBLIC_BUCKET` | `nbte-public` |
| `R2_PRIVATE_BUCKET` | `nbte-private` |
| `R2_PUBLIC_DOMAIN` | `files.nbte.gov.ng` (optional; without it, public links are signed and expire) |

The app will not start if `FILE_STORAGE=r2` is set while any required variable is missing.
`USE_X_ACCEL_REDIRECT` is ignored on R2: restricted files always stream through the
protected download view, which checks permissions first.

## Moving existing files

Copy each folder into its bucket, keeping the paths the same. For example, with `rclone`
set up for R2:

```
rclone copy ./media r2:nbte-public
rclone copy ./protected_media r2:nbte-private
```

The database stores paths, not URLs, so nothing else needs to change.

## Backups

The backup engine still zips the local media folders. When R2 holds the files, use R2's own
bucket versioning or a scheduled `rclone sync` to a second bucket. The database and the
`data.xlsx` workbook are unaffected.
