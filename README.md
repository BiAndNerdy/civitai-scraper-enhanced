
# civitai-scraper

Downloads bulk images/videos from Civitai with filtering options and directory segmentation.

## Usage

Create a new virtual environment and install required modules by running the following commands.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run the script with the following command.

```
python3 civitai-scraper.py --help

Usage: civitai-scraper.py [OPTIONS]

  Download images from Civitai API.

Options:
  -d, --debug BOOLEAN         Enable debug logging
  -s, --silent BOOLEAN        Disable logging
  -k, --api-key TEXT          API key for Civitai  [required]
  -u, --username TEXT         CivitAI username to scrape
  -o, --output-path TEXT      Path to save the images
  -z, --compress              Compress images to reduce file size
  -w, --workers INTEGER       Number of workers to use for downloading
  -l, --limit INTEGER         Maximum number of images to download
  -c, --cursor TEXT           Cursor to start downloading from
  --min-width INTEGER         Minimum width of the image
  --min-height INTEGER        Minimum height of the image
  --min-like INTEGER          Minimum number of likes
  --min-dislike INTEGER       Minimum number of dislikes
  --min-comment INTEGER       Minimum number of comments
  --min-hearts INTEGER        Minimum number of hearts
  --min-cry INTEGER           Minimum number of cry reactions
  --min-laugh INTEGER         Minimum number of laugh reactions
  --require-metadata BOOLEAN  Only download images with metadata
  --require-keywords TEXT     CSV of keywords to match the prompt and require
  --ignore-keywords TEXT      CSV of keywords to match the prompt and ignore
  --nsfw BOOLEAN              Include NSFW images
  --nsfw-only BOOLEAN         Only download NSFW images
  --segment-by-date           Segment images into directories by date
  --segment-by-rating         Segment images into directories by rating
  --avif                      Save images in AVIF format
  --no-prompt-files           Skip saving prompt text files alongside downloads
  --type [image|video]        Only download this media type
  --help                      Show this message and exit.
```

## Options

### `-u, --username`

The CivitAI username whose images you want to download. Optional — omitting it scrapes across all users.

### `--avif`

Save images in AVIF format instead of their original format. AVIF offers significantly better compression than JPEG. Combine with `--compress` to use lossy compression (quality 70); without it, lossless encoding is used.

### `--no-prompt-files`

By default, if an image has generation metadata, its prompt is saved as a `.txt` file alongside the image. Pass this flag to skip that entirely.

### `--type [image|video]`

Filter downloads to only images or only videos. Without this flag, both are downloaded.

## Reliability

Downloads use atomic writes — files are written to a temporary `.tmp` path and renamed into place only once fully complete. This prevents corrupt or partial files from being left on disk if a download fails mid-write. HTTP errors from the API are retried up to 3 times before giving up on a page.
