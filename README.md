
# civitai-scraper

Downloads bulk images/videos from CivitAI with filtering, segmentation, and config file support. Fork of [zealsprince/civitai-scraper](https://github.com/zealsprince/civitai-scraper).

## Setup

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```
python3 civitai-scraper.py [OPTIONS]

Options:
  --config PATH               Load CLI flags from a config file
  -k, --api-key TEXT          API key for CivitAI  [required]
  -u, --username TEXT         CivitAI username to scrape
  -o, --output-path TEXT      Path to save downloaded files
  -w, --workers INTEGER       Number of parallel download workers
  -l, --limit INTEGER         Maximum number of files to download
  -c, --cursor TEXT           Resume from a specific API cursor
  --sort [Newest|Most Reactions|Most Comments]
                              Sort order for results (default: Newest)
  --type [image|video]        Only download this media type
  --nsfw BOOLEAN              Include NSFW content
  --nsfw-only BOOLEAN         Only download NSFW content
  -z, --compress              Compress images to JPEG at 80% quality
  --avif                      Save images in AVIF format
  --no-prompt-files           Skip saving prompt text alongside downloads
  --segment-by-date           Organise downloads into YYYY-MM-DD subdirectories
  --segment-by-rating         Organise downloads into subdirectories by NSFW rating level
  --min-width INTEGER         Minimum image width
  --min-height INTEGER        Minimum image height
  --min-like INTEGER          Minimum likes
  --min-dislike INTEGER       Minimum dislikes
  --min-comment INTEGER       Minimum comments
  --min-hearts INTEGER        Minimum hearts
  --min-cry INTEGER           Minimum cry reactions
  --min-laugh INTEGER         Minimum laugh reactions
  --require-metadata BOOLEAN  Only download items with generation metadata
  --require-keywords TEXT     Only download items whose prompt contains one of these keywords
  --ignore-keywords TEXT      Skip items whose prompt contains any of these keywords
  -d, --debug BOOLEAN         Enable debug logging
  -s, --silent BOOLEAN        Disable all logging
  --help                      Show this message and exit.
```

All options can also be set via environment variables prefixed with `CIVITAI_SCRAPER_` (e.g. `CIVITAI_SCRAPER_API_KEY`).

## Config Files

Pass `--config path/to/config.txt` to load flags from a file. Lines starting with `#` are comments. CLI flags passed directly always override the config file.

```
# my config
--username someuser
--output-path ./downloads
--workers 8
--nsfw-only
--no-prompt-files
--type video
--sort Most Reactions
--ignore-keywords ./ignore.csv
```

## Keyword Filtering

`--require-keywords` and `--ignore-keywords` accept either a comma-separated string or a path to a file. Files can be CSV (comma-separated) or one keyword per line, or a mix of both. Matching is case-insensitive and applies only to items that have generation metadata — items without a prompt are not filtered out.

```bash
# inline
--ignore-keywords "beach,sunset,cityscape"

# file
--ignore-keywords ./ignore.csv
```

## NSFW Filtering

| Flag | API behaviour |
|------|--------------|
| *(neither)* | SFW content only |
| `--nsfw` | All content (SFW + all NSFW levels) |
| `--nsfw-only` | NSFW content only |

## Reliability

- **Atomic writes** — files are written to a `.tmp` path and renamed into place only when fully complete, preventing partial files on failure or interruption.
- **HTTP retry** — API pagination errors (including 5xx responses) are retried up to 3 times with a 30-second delay before giving up.
- **Video CDN fallback** — CivitAI occasionally returns a thumbnail image from a video item's URL. When detected by checking the response `Content-Type`, the script automatically retries against the correct CDN path to retrieve the actual video.
- **Download logging** — each downloaded file logs its Content-Type, declared size, and actual bytes received, making it easy to spot unexpected content.

## Output

Each downloaded file is logged alongside its content type and size:

```
INFO:root:Downloaded 126383491 [ok, video/mp4, declared=5208895b, actual=5208895b]
INFO:root:Downloaded 122909919 [b2 fallback, video/mp4, declared=4821334b, actual=4821334b]
```

Previously downloaded files are tracked in `downloaded.log` inside the output directory and skipped on subsequent runs.
